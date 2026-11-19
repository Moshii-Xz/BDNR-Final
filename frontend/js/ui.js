// ─────────────────────────────────────────────────────────────────────
// ui.js — utilidades de presentación compartidas por las vistas.
// ─────────────────────────────────────────────────────────────────────

/** Escapa texto para insertarlo en HTML (todo dato del API pasa por aquí). */
export function esc(valor) {
  return String(valor ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const formatoCOP = new Intl.NumberFormat("es-CO", {
  style: "currency", currency: "COP", maximumFractionDigits: 0,
});
export const precio = (n) => formatoCOP.format(Number(n) || 0);

const formatoFecha = new Intl.DateTimeFormat("es-CO", {
  dateStyle: "medium", timeStyle: "short", timeZone: "America/Bogota",
});
export const fecha = (iso) => (iso ? formatoFecha.format(new Date(iso)) : "");

const formatoDia = new Intl.DateTimeFormat("es-CO", {
  weekday: "long", day: "numeric", month: "long", timeZone: "America/Bogota",
});
export const dia = (yyyyMmDd) => formatoDia.format(new Date(`${yyyyMmDd}T12:00:00-05:00`));

/** Segundos que faltan para la medianoche de Bogotá (UTC-5, sin horario de verano). */
export function segundosHastaMedianocheBogota(ahora = new Date()) {
  const bogota = new Date(ahora.getTime() - 5 * 3600 * 1000);
  const transcurridos = bogota.getUTCHours() * 3600 + bogota.getUTCMinutes() * 60 + bogota.getUTCSeconds();
  return 86400 - transcurridos;
}

export function relojHMS(segundos) {
  const h = Math.floor(segundos / 3600);
  const m = Math.floor((segundos % 3600) / 60);
  const s = segundos % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

// ── Íconos de línea por categoría (no hay fotos de producto) ──────────
const TRAZOS = {
  Laptops: '<rect x="14" y="14" width="36" height="24" rx="2"/><path d="M8 46h48l-4-6H12z"/>',
  Audio: '<path d="M14 38v-6a18 18 0 0 1 36 0v6"/><rect x="10" y="36" width="10" height="14" rx="3"/><rect x="44" y="36" width="10" height="14" rx="3"/>',
  Monitores: '<rect x="8" y="12" width="48" height="30" rx="2"/><path d="M26 52h12M32 42v10"/>',
  Celulares: '<rect x="20" y="8" width="24" height="48" rx="4"/><path d="M29 50h6"/>',
  "Periféricos": '<rect x="6" y="22" width="36" height="20" rx="3"/><path d="M12 28h2M18 28h2M24 28h2M30 28h2M12 34h20"/><rect x="46" y="20" width="12" height="22" rx="6"/><path d="M52 20v7"/>',
  Accesorios: '<rect x="18" y="10" width="28" height="44" rx="5"/><path d="M28 10V6h8v4M26 26h12M26 34h12"/>',
};
const TRAZO_GENERICO = '<rect x="12" y="12" width="40" height="40" rx="6"/><path d="M22 32h20M32 22v20"/>';

export function icono(categoria, clase = "") {
  const trazo = TRAZOS[categoria] || TRAZO_GENERICO;
  return `<svg class="icono-cat ${clase}" viewBox="0 0 64 64" fill="none" stroke="currentColor"
    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${trazo}</svg>`;
}

const NOMBRE_FUENTE = { cache: "Redis", redis: "Redis", dynamodb: "DynamoDB", elasticsearch: "Elasticsearch" };
export function fuente(f) {
  if (!f) return "";
  const clave = f === "redis" ? "cache" : f;
  return `<span class="fuente fuente--${esc(clave)}">${esc(NOMBRE_FUENTE[f] || f)}</span>`;
}

// ── Avisos ────────────────────────────────────────────────────────────
export function aviso(texto, tipo = "ok") {
  const zona = document.getElementById("avisos");
  const nodo = document.createElement("div");
  nodo.className = `aviso aviso--${tipo}`;
  nodo.setAttribute("role", tipo === "error" ? "alert" : "status");
  nodo.textContent = texto;
  zona.append(nodo);
  setTimeout(() => nodo.classList.add("aviso--saliendo"), 3600);
  setTimeout(() => nodo.remove(), 4000);
}

/** Estado de error dentro de una vista, con la acción para resolverlo. */
export function bloqueError(error, accion = "") {
  return `<div class="vacio vacio--error">
    <p class="vacio__titulo">${esc(error.message || error)}</p>
    ${accion}
  </div>`;
}

export function cargando(texto = "Cargando") {
  return `<p class="cargando" aria-live="polite">${esc(texto)}…</p>`;
}

/** Ejecuta fn mientras el botón muestra que está trabajando. */
export async function conBoton(boton, fn) {
  if (!boton) return fn();
  const original = boton.textContent;
  boton.disabled = true;
  boton.setAttribute("aria-busy", "true");
  try {
    return await fn();
  } finally {
    boton.disabled = false;
    boton.removeAttribute("aria-busy");
    boton.textContent = original;
  }
}
