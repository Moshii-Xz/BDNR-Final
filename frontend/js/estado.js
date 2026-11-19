// ─────────────────────────────────────────────────────────────────────
// estado.js — usuario activo, catálogo en memoria y contador del carrito.
// No hay autenticación en el proyecto: el usuario elegido viaja en el
// header X-Username (carrito y checkout) o en la ruta (/users/{u}/...).
// ─────────────────────────────────────────────────────────────────────
import { api, enc } from "./api.js";

const CLAVE_USUARIO = "marea.usuario";
const CLAVE_CONOCIDOS = "marea.usuarios";
const USUARIOS_SEED = ["camilo_dev", "caro_dev", "ana_compras", "beto_tech"];

function leer(clave, porDefecto) {
  try {
    const v = localStorage.getItem(clave);
    return v ? JSON.parse(v) : porDefecto;
  } catch {
    return porDefecto;
  }
}
function guardar(clave, valor) {
  try { localStorage.setItem(clave, JSON.stringify(valor)); } catch { /* modo privado */ }
}

const oyentes = new Set();
const avisar = () => oyentes.forEach((fn) => fn());
export const alCambiar = (fn) => oyentes.add(fn);

export const estado = {
  usuario: leer(CLAVE_USUARIO, "camilo_dev"),
  conocidos: [...new Set([...USUARIOS_SEED, ...leer(CLAVE_CONOCIDOS, [])])],
  unidadesCarrito: 0,
  catalogo: new Map(),     // id → producto (de GET /products)
  promo: null,             // promoción de hoy o null
};

export function cambiarUsuario(username) {
  estado.usuario = username;
  guardar(CLAVE_USUARIO, username);
  if (!estado.conocidos.includes(username)) {
    estado.conocidos.push(username);
    guardar(CLAVE_CONOCIDOS, estado.conocidos.filter((u) => !USUARIOS_SEED.includes(u)));
  }
  refrescarCarrito();
  avisar();
}

export async function refrescarCarrito() {
  try {
    const c = await api.get("/cart", { usuario: estado.usuario });
    estado.unidadesCarrito = c.total_unidades;
  } catch {
    estado.unidadesCarrito = 0;
  }
  avisar();
}

export async function cargarCatalogo(forzar = false) {
  if (estado.catalogo.size && !forzar) return estado.catalogo;
  const { productos } = await api.get("/products");
  estado.catalogo = new Map(productos.map((p) => [p.id, p]));
  return estado.catalogo;
}

export async function cargarPromo() {
  try {
    estado.promo = await api.get("/promotions/today");
  } catch {
    estado.promo = null;
  }
  return estado.promo;
}

/** Precio que se cobraría hoy (el checkout aplica la promoción del día). */
export function precioVigente(producto) {
  const p = estado.promo;
  if (p && p.productos.some((x) => x.productId === producto.id)) {
    return { precio: Math.round(producto.precio * (100 - p.descuento_pct)) / 100, enPromo: true };
  }
  return { precio: producto.precio, enPromo: false };
}

export async function esFavorito(productId) {
  try {
    const w = await api.get(`/users/${enc(estado.usuario)}/wishlist`);
    return w.productos.some((p) => p.productId === productId);
  } catch {
    return false;
  }
}
