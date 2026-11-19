// ─────────────────────────────────────────────────────────────────────
// app.js — arranque, router por hash, cabecera e inspector "Por dentro".
// ─────────────────────────────────────────────────────────────────────
import { conectar, peticiones, alRegistrar } from "./api.js";
import { estado, alCambiar, cambiarUsuario, refrescarCarrito } from "./estado.js";
import { esc, fuente, bloqueError } from "./ui.js";
import * as vistas from "./vistas.js";

const RUTAS = [
  [/^$/, vistas.inicio, "inicio"],
  [/^producto\/([^/]+)$/, vistas.producto, "inicio", ["id"]],
  [/^buscar$/, vistas.buscar, "inicio"],
  [/^carrito$/, vistas.carrito, "carrito"],
  [/^pedidos$/, vistas.pedidos, "pedidos"],
  [/^pedido\/([^/]+)$/, vistas.pedido, "pedidos", ["id"]],
  [/^cuenta$/, vistas.cuenta, "cuenta"],
  [/^gestion$/, vistas.gestion, "gestion"],
];

const main = document.getElementById("contenido");
let limpiezas = [];

async function navegar() {
  limpiezas.forEach((fn) => fn());
  limpiezas = [];

  const [ruta, qs = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const query = new URLSearchParams(qs);
  let vista = null;
  let seccion = "";
  const params = {};
  for (const [re, fn, sec, nombres = []] of RUTAS) {
    const m = ruta.match(re);
    if (m) {
      vista = fn;
      seccion = sec;
      nombres.forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
      break;
    }
  }

  document.querySelectorAll("[data-seccion]").forEach((a) => {
    if (a.dataset.seccion === seccion) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const busqueda = document.getElementById("q");
  if (ruta === "buscar") busqueda.value = query.get("q") || "";

  // Contenedor nuevo por navegación: los listeners de la vista anterior
  // se van con su nodo.
  const contenedor = document.createElement("div");
  contenedor.className = "vista";
  main.replaceChildren(contenedor);
  main.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });

  if (!vista) {
    contenedor.innerHTML = bloqueError("Esta página no existe.", '<a class="boton" href="#/">Ir al inicio</a>');
    return;
  }
  await vista(contenedor, {
    params,
    query,
    alSalir: (fn) => limpiezas.push(fn),
    recargar: navegar,
  });
}

// ── Cabecera: usuario activo y contador del carrito ───────────────────

function pintarCabecera() {
  const selector = document.getElementById("usuario");
  selector.innerHTML = estado.conocidos
    .map((u) => `<option value="${esc(u)}" ${u === estado.usuario ? "selected" : ""}>${esc(u)}</option>`)
    .join("");
  const contador = document.getElementById("contador-carrito");
  contador.textContent = estado.unidadesCarrito;
  contador.hidden = !estado.unidadesCarrito;
  document.getElementById("enlace-carrito").setAttribute(
    "aria-label", `Carrito, ${estado.unidadesCarrito} ${estado.unidadesCarrito === 1 ? "unidad" : "unidades"}`,
  );
}

document.getElementById("usuario").addEventListener("change", (ev) => {
  cambiarUsuario(ev.target.value);
  navegar();   // las vistas dependen del usuario (carrito, pedidos, cuenta)
});

document.getElementById("buscador").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (q) location.hash = `#/buscar?q=${encodeURIComponent(q)}`;
});

// ── Inspector "Por dentro" ────────────────────────────────────────────

const panel = document.getElementById("inspector");
const lista = document.getElementById("inspector-lista");
const botonInspector = document.getElementById("abrir-inspector");
const cuentaInspector = document.getElementById("inspector-cuenta");

function filaPeticion(p, i) {
  const ok = p.status >= 200 && p.status < 300;
  return `<li class="peticion${ok ? "" : " peticion--error"}">
    <details>
      <summary>
        <span class="peticion__linea">
          <span class="peticion__metodo">${esc(p.metodo)}</span>
          <span class="peticion__ruta">${esc(p.ruta)}</span>
        </span>
        <span class="peticion__meta">
          <span class="peticion__estado">${p.status || "sin red"}</span>
          <span>${p.ms} ms</span>
          ${fuente(p.fuente)}
        </span>
        <span class="peticion__patron">${esc(p.patron)}</span>
      </summary>
      ${p.llave ? `<p class="peticion__llave">${esc(p.llave)}</p>` : ""}
      <pre class="peticion__cuerpo" id="cuerpo-${i}">${esc(JSON.stringify(p.cuerpo, null, 2).slice(0, 4000))}</pre>
    </details>
  </li>`;
}

function pintarInspector() {
  const todas = peticiones();
  cuentaInspector.textContent = todas.length;
  if (panel.hidden) return;
  lista.innerHTML = todas.length
    ? todas.map(filaPeticion).join("")
    : '<li class="nota">Navegue por la tienda y aquí aparecerá cada petición.</li>';
}

function abrirInspector(abrir) {
  panel.hidden = !abrir;
  botonInspector.setAttribute("aria-expanded", String(abrir));
  document.body.classList.toggle("con-inspector", abrir);
  try { localStorage.setItem("marea.inspector", abrir ? "1" : "0"); } catch { /* sin almacenamiento */ }
  if (abrir) pintarInspector();
}

botonInspector.addEventListener("click", () => abrirInspector(panel.hidden));
document.getElementById("cerrar-inspector").addEventListener("click", () => {
  abrirInspector(false);
  botonInspector.focus();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !panel.hidden) abrirInspector(false);
});

// Repintar en lote: una vista puede disparar varias peticiones seguidas
let pendiente = false;
alRegistrar(() => {
  if (pendiente) return;
  pendiente = true;
  requestAnimationFrame(() => { pendiente = false; pintarInspector(); });
});

// ── Arranque ──────────────────────────────────────────────────────────

async function arrancar() {
  pintarCabecera();
  alCambiar(pintarCabecera);
  // ?inspector=1 en la URL lo abre (útil para presentar la demo con el panel)
  let abrir = new URLSearchParams(location.search).get("inspector") === "1";
  try {
    abrir = abrir || localStorage.getItem("marea.inspector") === "1";
  } catch { /* sin almacenamiento */ }
  if (abrir) abrirInspector(true);

  try {
    await conectar();
  } catch (e) {
    main.innerHTML = bloqueError(e, `<p class="nota">Revise que el emulador esté arriba y que el stack esté desplegado:
      <code>docker compose exec cdk-dev cdklocal deploy</code>. Luego recargue la página.</p>`);
    return;
  }
  refrescarCarrito();
  window.addEventListener("hashchange", navegar);
  navegar();
}

arrancar();
