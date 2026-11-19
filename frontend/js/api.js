// ─────────────────────────────────────────────────────────────────────
// api.js — cliente del API Gateway del emulador.
// Todas las llamadas pasan por aquí: así el inspector "Por dentro" puede
// mostrar cada petición con su patrón de acceso y la fuente del dato.
// ─────────────────────────────────────────────────────────────────────

const NOMBRE_API = "ProyectoFinal-API";
let base = null;

export class ErrorApi extends Error {
  constructor(status, mensaje, cuerpo) {
    super(mensaje);
    this.status = status;
    this.cuerpo = cuerpo;
  }
}

/** Busca el RestApi vigente (el api-id cambia en cada `cdklocal deploy`). */
export async function conectar() {
  const r = await fetch("/apis-desplegadas");
  if (!r.ok) throw new Error(`El emulador respondió ${r.status} al listar los APIs`);
  const datos = await r.json();
  const apis = (datos.item || [])
    .filter((a) => a.name === NOMBRE_API)
    .sort((a, b) => b.createdDate - a.createdDate);
  if (!apis.length) throw new Error("No hay ningún API desplegado. Ejecute cdklocal deploy en cdk-dev.");
  base = `/restapis/${apis[0].id}/prod/_user_request_`;
  return apis[0].id;
}

// ── Patrones de acceso: qué resuelve cada ruta y con qué llave ────────
// La fuente por defecto se usa cuando la respuesta no trae "_fuente".
const PATRONES = [
  ["GET", /^\/health$/, "Estado de los servicios", "DynamoDB + Redis + ES", null],
  ["GET", /^\/products\/ranking/, "Ranking de más vistos", "Sorted Set ranking:productos:vistas", "redis"],
  ["GET", /^\/products\/[^/]+\/reviews$/, "#4 Reseñas del producto", "Query PK=PRODUCT#id, SK begins_with REVIEW#, orden inverso", "dynamodb"],
  ["POST", /^\/products\/[^/]+\/reviews$/, "#4 Nueva reseña", "PutItem PK=PRODUCT#id, SK=REVIEW#fecha#usuario", "dynamodb"],
  ["GET", /^\/products\/[^/]+$/, "#1 Detalle de producto", "Cache-Aside → GetItem PK=SK=PRODUCT#id", "dynamodb"],
  ["DELETE", /^\/products\/[^/]+$/, "Eliminar producto", "DeleteItem + reseñas + espejo en ES", "dynamodb"],
  ["GET", /^\/products$/, "#3 Catálogo completo", "Scan filtrado por PRODUCT#", "dynamodb"],
  ["POST", /^\/products$/, "Crear producto", "PutItem con GSI1/GSI2 + índice en ES", "dynamodb"],
  ["GET", /^\/categories\/[^/]+\/products$/, "#2 Productos por categoría", "Query GSI1 CAT#categoria", "dynamodb"],
  ["GET", /^\/brands\/[^/]+\/products/, "#5 Productos por marca", "Query GSI2 BRAND#marca (+ begins_with CAT#)", "dynamodb"],
  ["GET", /^\/search/, "#6 Búsqueda libre", "multi_match con fuzziness", "elasticsearch"],
  ["POST", /^\/search\/reindex$/, "Reconstruir índice", "Scan de productos + Bulk API", "elasticsearch"],
  ["GET", /^\/cart$/, "#7 Ver carrito", "Query PK=USER#u, SK begins_with CART#", "dynamodb"],
  ["PUT", /^\/cart\/[^/]+$/, "#7 Poner en el carrito", "PutItem PK=USER#u, SK=CART#producto", "dynamodb"],
  ["DELETE", /^\/cart\/[^/]+$/, "#7 Quitar del carrito", "DeleteItem PK=USER#u, SK=CART#producto", "dynamodb"],
  ["GET", /^\/coupons\/[^/]+$/, "#8 Validar cupón", "GetItem PK=SK=COUPON#código", "dynamodb"],
  ["POST", /^\/coupons$/, "Crear cupón", "PutItem PK=SK=COUPON#código", "dynamodb"],
  ["POST", /^\/users$/, "Registrar usuario", "PutItem condicional USER#u", "dynamodb"],
  ["GET", /^\/users\/[^/]+$/, "#9 Perfil", "GetItem PK=SK=USER#u", "dynamodb"],
  ["GET", /^\/users\/[^/]+\/addresses$/, "#10 Direcciones", "Query PK=USER#u, SK begins_with ADDRESS#", "dynamodb"],
  ["PUT", /^\/users\/[^/]+\/addresses\/[^/]+$/, "#10 Guardar dirección", "PutItem SK=ADDRESS#nombre", "dynamodb"],
  ["DELETE", /^\/users\/[^/]+\/addresses\/[^/]+$/, "#10 Borrar dirección", "DeleteItem SK=ADDRESS#nombre", "dynamodb"],
  ["GET", /^\/users\/[^/]+\/wishlist$/, "#11 Lista de deseos", "Query PK=USER#u, SK begins_with WISH#", "dynamodb"],
  ["PUT", /^\/users\/[^/]+\/wishlist\/[^/]+$/, "#11 Marcar favorito", "PutItem SK=WISH#producto", "dynamodb"],
  ["DELETE", /^\/users\/[^/]+\/wishlist\/[^/]+$/, "#11 Quitar favorito", "DeleteItem SK=WISH#producto", "dynamodb"],
  ["POST", /^\/orders$/, "#12 Checkout", "TransactWriteItems: orden + líneas + carrito + stock", "dynamodb"],
  ["GET", /^\/users\/[^/]+\/orders\/ultima$/, "#13 Última orden", "Query inverso Limit=1 + prefijo de la orden", "dynamodb"],
  ["GET", /^\/users\/[^/]+\/orders$/, "#14 Historial de órdenes", "Query PK=USER#u, SK begins_with ORDER#, inverso", "dynamodb"],
  ["GET", /^\/orders\/[^/]+$/, "#15 Orden por número", "Cache-Aside → Query GSI1 ORDER#id", "dynamodb"],
  ["GET", /^\/promotions\/today$/, "#16 Promoción del día", "Redis (Write-Through) → GetItem PROMO#fecha", "dynamodb"],
  ["POST", /^\/promotions$/, "#17 Publicar promoción", "PutItem PROMO#fecha + Write-Through a Redis", "dynamodb"],
];

function patronDe(metodo, ruta) {
  const limpia = ruta.split("?")[0];
  for (const [m, re, nombre, llave, fuente] of PATRONES) {
    if (m === metodo && re.test(limpia)) return { nombre, llave, fuente };
  }
  return { nombre: "Otra ruta", llave: "", fuente: null };
}

// ── Registro de peticiones para el inspector ──────────────────────────
const registro = [];
const oyentes = new Set();
export const peticiones = () => registro;
export function alRegistrar(fn) {
  oyentes.add(fn);
  return () => oyentes.delete(fn);
}

function anotar(entrada) {
  registro.unshift(entrada);
  registro.length = Math.min(registro.length, 60);
  oyentes.forEach((fn) => fn(entrada));
}

/**
 * Llama al API. Lanza ErrorApi si el estado no es 2xx (con el mensaje que
 * devuelve la Lambda en "error").
 */
export async function pedir(metodo, ruta, { body, usuario } = {}) {
  if (!base) await conectar();
  const headers = { "Content-Type": "application/json" };
  if (usuario) headers["X-Username"] = usuario;

  const inicio = performance.now();
  let status = 0;
  let datos = null;
  try {
    const r = await fetch(base + ruta, {
      method: metodo,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    status = r.status;
    const texto = await r.text();
    try {
      datos = texto ? JSON.parse(texto) : {};
    } catch {
      datos = { error: texto.slice(0, 200) };
    }
  } catch (e) {
    datos = { error: `Sin conexión con el API (${e.message})` };
  }
  const ms = Math.round(performance.now() - inicio);

  const patron = patronDe(metodo, ruta);
  anotar({
    metodo, ruta, status, ms,
    patron: patron.nombre,
    llave: patron.llave,
    fuente: (datos && datos._fuente) || patron.fuente,
    cuerpo: datos,
    hora: new Date(),
  });

  if (status < 200 || status >= 300) {
    const mensaje = (datos && datos.error) || `El servidor respondió ${status}`;
    throw new ErrorApi(status, mensaje, datos);
  }
  return datos;
}

export const api = {
  get: (ruta, op) => pedir("GET", ruta, op),
  post: (ruta, body, op = {}) => pedir("POST", ruta, { ...op, body }),
  put: (ruta, body, op = {}) => pedir("PUT", ruta, { ...op, body }),
  del: (ruta, op) => pedir("DELETE", ruta, op),
};

export const enc = encodeURIComponent;
