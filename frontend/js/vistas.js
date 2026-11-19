// ─────────────────────────────────────────────────────────────────────
// vistas.js — cada pantalla de la tienda. Una vista recibe el <main> y un
// contexto { params, query, alSalir } y pinta su contenido.
// ─────────────────────────────────────────────────────────────────────
import { api, enc, ErrorApi } from "./api.js";
import {
  estado, cargarCatalogo, cargarPromo, precioVigente, refrescarCarrito, esFavorito, cambiarUsuario,
} from "./estado.js";
import {
  esc, precio, fecha, dia, icono, fuente, aviso, bloqueError, cargando, conBoton,
  segundosHastaMedianocheBogota, relojHMS,
} from "./ui.js";

const u = () => enc(estado.usuario);

// ── Piezas reutilizables ──────────────────────────────────────────────

function etiquetaPrecio(p) {
  const { precio: vigente, enPromo } = precioVigente(p);
  return enPromo
    ? `<span class="precio precio--promo">${precio(vigente)}</span>
       <s class="precio-antes">${precio(p.precio)}</s>`
    : `<span class="precio">${precio(p.precio)}</span>`;
}

function losa(p) {
  const { enPromo } = precioVigente(p);
  return `<li class="losa${enPromo ? " losa--promo" : ""}">
    <a class="losa__enlace" href="#/producto/${enc(p.id)}">
      <span class="losa__arte">${icono(p.categoria)}</span>
      <span class="losa__marca">${esc(p.marca)}</span>
      <span class="losa__nombre">${esc(p.nombre)}</span>
      <span class="losa__precio">${etiquetaPrecio(p)}</span>
      ${enPromo ? '<span class="losa__sello">Producto del día</span>' : ""}
    </a>
  </li>`;
}

function rejilla(productos, vacio = "No hay productos aquí todavía.") {
  if (!productos.length) return `<div class="vacio"><p class="vacio__titulo">${esc(vacio)}</p></div>`;
  return `<ul class="rejilla">${productos.map(losa).join("")}</ul>`;
}

function estrellas(n) {
  const llenas = Math.round(n);
  return `<span class="estrellas" aria-label="${n.toFixed(1)} de 5">
    ${"★".repeat(llenas)}<span class="estrellas__vacias">${"★".repeat(5 - llenas)}</span></span>`;
}

// ── Inicio: promoción del día + catálogo ──────────────────────────────

function heroPromo(promo, alSalir) {
  if (!promo) {
    return `<section class="hero hero--sin-promo" aria-labelledby="hero-titulo">
      <h1 id="hero-titulo" class="hero__titulo">Hoy no hay producto del día</h1>
      <p class="hero__texto">Cuando se publique la promoción aparecerá aquí, ya guardada en Redis.</p>
      <a class="boton boton--claro" href="#/gestion">Publicar la promoción</a>
    </section>`;
  }
  alSalir(iniciarReloj());
  const tarjetas = promo.productos.map((p) => `
    <a class="promo-producto" href="#/producto/${enc(p.productId)}">
      <span class="promo-producto__arte">${icono(estado.catalogo.get(p.productId)?.categoria)}</span>
      <span class="promo-producto__nombre">${esc(p.nombre)}</span>
      <span class="promo-producto__precios">
        <span class="promo-producto__ahora">${precio(p.precio_promocion)}</span>
        <s>${precio(p.precio)}</s>
      </span>
    </a>`).join("");

  return `<section class="hero" aria-labelledby="hero-titulo">
    <div class="hero__cabeza">
      <h1 id="hero-titulo" class="hero__titulo">Producto del día: ${esc(promo.descuento_pct)}% menos</h1>
      <p class="hero__texto">${esc(dia(promo.fecha))}. Dos productos con descuento hasta la medianoche.</p>
    </div>
    <div class="hero__reloj" role="timer" aria-label="Tiempo restante de la promoción">
      <span class="reloj" id="reloj">${relojHMS(segundosHastaMedianocheBogota())}</span>
      <span class="reloj__nota">Termina a las 12:00 a. m. (Bogotá). Es el mismo plazo que su TTL en Redis.
        ${fuente(promo._fuente)}</span>
    </div>
    <div class="hero__productos">${tarjetas}</div>
  </section>`;
}

function iniciarReloj() {
  const id = setInterval(() => {
    const nodo = document.getElementById("reloj");
    if (nodo) nodo.textContent = relojHMS(segundosHastaMedianocheBogota());
  }, 1000);
  return () => clearInterval(id);
}

export async function inicio(main, { alSalir }) {
  main.innerHTML = cargando("Cargando la tienda");
  try {
    await Promise.all([cargarCatalogo(true), cargarPromo()]);
  } catch (e) {
    main.innerHTML = bloqueError(e, '<a class="boton" href="#/">Reintentar</a>');
    return;
  }

  const productos = [...estado.catalogo.values()];
  const categorias = [...new Set(productos.map((p) => p.categoria))].sort((a, b) => a.localeCompare(b, "es"));
  const marcas = [...new Set(productos.map((p) => p.marca))].sort((a, b) => a.localeCompare(b, "es"));

  main.innerHTML = `
    ${heroPromo(estado.promo, alSalir)}
    <div class="catalogo">
      <section class="catalogo__principal" aria-labelledby="cat-titulo">
        <div class="catalogo__cabeza">
          <h2 id="cat-titulo" class="seccion-titulo">Catálogo</h2>
          <label class="campo-inline">Marca
            <select id="filtro-marca">
              <option value="">Todas</option>
              ${marcas.map((m) => `<option>${esc(m)}</option>`).join("")}
            </select>
          </label>
        </div>
        <nav class="pestanas" aria-label="Categorías">
          <button type="button" class="pestana" data-cat="" aria-pressed="true">Todo</button>
          ${categorias.map((c) => `<button type="button" class="pestana" data-cat="${esc(c)}" aria-pressed="false">${esc(c)}</button>`).join("")}
        </nav>
        <p class="consulta" id="consulta"></p>
        <div id="resultado">${rejilla(productos)}</div>
      </section>
      <aside class="ranking" aria-labelledby="rank-titulo">
        <h2 id="rank-titulo" class="seccion-titulo">Lo más visto</h2>
        <div id="ranking">${cargando()}</div>
      </aside>
    </div>`;

  let categoria = "";
  let marca = "";
  const resultado = main.querySelector("#resultado");
  const consulta = main.querySelector("#consulta");

  async function filtrar() {
    main.querySelectorAll(".pestana").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === categoria)));
    let lista;
    try {
      if (marca) {
        const q = categoria ? `?categoria=${enc(categoria)}` : "";
        lista = (await api.get(`/brands/${enc(marca)}/products${q}`)).productos;
        consulta.textContent = `Índice GSI2: marca ${marca}${categoria ? ` y categoría ${categoria}` : ""}`;
      } else if (categoria) {
        lista = (await api.get(`/categories/${enc(categoria)}/products`)).productos;
        consulta.textContent = `Índice GSI1: categoría ${categoria}`;
      } else {
        lista = [...estado.catalogo.values()];
        consulta.textContent = "";
      }
      resultado.innerHTML = rejilla(lista, "Ningún producto cumple ese filtro.");
    } catch (e) {
      resultado.innerHTML = bloqueError(e);
    }
  }

  main.querySelector(".pestanas").addEventListener("click", (ev) => {
    const b = ev.target.closest(".pestana");
    if (!b) return;
    categoria = b.dataset.cat;
    filtrar();
  });
  main.querySelector("#filtro-marca").addEventListener("change", (ev) => {
    marca = ev.target.value;
    filtrar();
  });

  try {
    const { ranking } = await api.get("/products/ranking?limite=5");
    const filas = ranking.filter((r) => estado.catalogo.has(r.productId));
    main.querySelector("#ranking").innerHTML = filas.length
      ? `<ol class="ranking__lista">${filas.map((r) => {
          const p = estado.catalogo.get(r.productId);
          return `<li><a href="#/producto/${enc(p.id)}"><span class="ranking__nombre">${esc(p.nombre)}</span>
            <span class="ranking__vistas">${r.vistas} ${r.vistas === 1 ? "visita" : "visitas"}</span></a></li>`;
        }).join("")}</ol>
        <p class="nota">Contador en un Sorted Set de Redis; sube con cada ficha abierta.</p>`
      : '<p class="nota">Abra algunas fichas de producto y aparecerán aquí.</p>';
  } catch {
    main.querySelector("#ranking").innerHTML = '<p class="nota">Ranking no disponible.</p>';
  }
}

// ── Ficha de producto ─────────────────────────────────────────────────

export async function producto(main, { params }) {
  main.innerHTML = cargando("Cargando el producto");
  let p;
  try {
    [p] = await Promise.all([api.get(`/products/${enc(params.id)}`), estado.promo ? null : cargarPromo()]);
  } catch (e) {
    main.innerHTML = bloqueError(
      e.status === 404 ? "Este producto ya no está en el catálogo." : e,
      '<a class="boton" href="#/">Volver al catálogo</a>',
    );
    return;
  }

  const { enPromo } = precioVigente(p);
  main.innerHTML = `
    <article class="ficha">
      <div class="ficha__arte${enPromo ? " ficha__arte--promo" : ""}">${icono(p.categoria, "icono-cat--grande")}</div>
      <div class="ficha__info">
        <p class="ficha__ruta"><a href="#/">Catálogo</a> / ${esc(p.categoria)}</p>
        <p class="ficha__marca">${esc(p.marca)}</p>
        <h1 class="ficha__nombre">${esc(p.nombre)}</h1>
        <p class="ficha__precio">${etiquetaPrecio(p)}</p>
        ${enPromo ? `<p class="ficha__promo">Producto del día: ${esc(estado.promo.descuento_pct)}% menos hasta la medianoche.</p>` : ""}
        <p class="ficha__desc">${esc(p.descripcion)}</p>
        <p class="ficha__stock">${p.stock > 0 ? `${p.stock} disponibles` : "Agotado"}</p>
        <form class="ficha__compra" id="form-compra">
          <label class="campo-inline">Cantidad
            <input type="number" name="cantidad" min="1" max="${Math.max(1, Math.min(99, p.stock))}" value="1" required>
          </label>
          <button class="boton" type="submit" ${p.stock > 0 ? "" : "disabled"}>Agregar al carrito</button>
          <button class="boton boton--borde" type="button" id="favorito" aria-pressed="false">Guardar en favoritos</button>
        </form>
        <p class="nota">Este detalle llegó de ${fuente(p._fuente)}: la primera visita lo lee de DynamoDB
          y lo guarda en Redis por 5 minutos.</p>
      </div>
    </article>
    <section class="resenas" aria-labelledby="res-titulo">
      <h2 id="res-titulo" class="seccion-titulo">Reseñas</h2>
      <div id="lista-resenas">${cargando()}</div>
      <form class="resena-form" id="form-resena">
        <h3 class="subtitulo">Escribir una reseña como ${esc(estado.usuario)}</h3>
        <fieldset class="calificar">
          <legend>Calificación</legend>
          <span class="calificar__estrellas">
            ${[5, 4, 3, 2, 1].map((n) => `<input type="radio" id="cal-${n}" name="calificacion" value="${n}" ${n === 5 ? "checked" : ""}><label for="cal-${n}" title="${n} de 5"><span class="sr">${n} de 5</span><span aria-hidden="true">★</span></label>`).join("")}
          </span>
        </fieldset>
        <label class="campo">Comentario
          <textarea name="comentario" rows="3" maxlength="500" placeholder="¿Qué te pareció?"></textarea>
        </label>
        <button class="boton" type="submit">Publicar reseña</button>
      </form>
    </section>`;

  // Carrito
  main.querySelector("#form-compra").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const boton = ev.submitter;
    const cantidad = Number(new FormData(ev.target).get("cantidad"));
    await conBoton(boton, async () => {
      try {
        await api.put(`/cart/${enc(p.id)}`, { cantidad }, { usuario: estado.usuario });
        aviso(`${p.nombre}: ${cantidad} en el carrito`);
        refrescarCarrito();
      } catch (e) {
        aviso(e.message, "error");
      }
    });
  });

  // Favorito
  const botonFav = main.querySelector("#favorito");
  const pintarFav = (activo) => {
    botonFav.setAttribute("aria-pressed", String(activo));
    botonFav.textContent = activo ? "Quitar de favoritos" : "Guardar en favoritos";
  };
  esFavorito(p.id).then(pintarFav);
  botonFav.addEventListener("click", async () => {
    const activo = botonFav.getAttribute("aria-pressed") === "true";
    try {
      if (activo) await api.del(`/users/${u()}/wishlist/${enc(p.id)}`);
      else await api.put(`/users/${u()}/wishlist/${enc(p.id)}`);
      pintarFav(!activo);
      aviso(activo ? "Quitado de favoritos" : "Guardado en favoritos");
    } catch (e) {
      aviso(e.status === 404 ? "Cree primero la cuenta en Mi cuenta" : e.message, "error");
    }
  });

  // Reseñas
  async function pintarResenas() {
    const lista = main.querySelector("#lista-resenas");
    try {
      const { resenas } = await api.get(`/products/${enc(p.id)}/reviews`);
      if (!resenas.length) {
        lista.innerHTML = '<p class="nota">Todavía no hay reseñas. La primera puede ser la tuya.</p>';
        return;
      }
      const promedio = resenas.reduce((s, r) => s + r.calificacion, 0) / resenas.length;
      lista.innerHTML = `
        <p class="resenas__promedio">${estrellas(promedio)} ${promedio.toFixed(1)} de 5, ${resenas.length} ${resenas.length === 1 ? "reseña" : "reseñas"}</p>
        <ul class="resenas__lista">${resenas.map((r) => `
          <li class="resena">
            <p class="resena__cabeza">${estrellas(r.calificacion)} <strong>${esc(r.username)}</strong>
              <time datetime="${esc(r.fecha)}">${esc(fecha(r.fecha))}</time></p>
            ${r.comentario ? `<p>${esc(r.comentario)}</p>` : ""}
          </li>`).join("")}</ul>`;
    } catch (e) {
      lista.innerHTML = bloqueError(e);
    }
  }
  pintarResenas();

  main.querySelector("#form-resena").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const datos = new FormData(ev.target);
    await conBoton(ev.submitter, async () => {
      try {
        await api.post(`/products/${enc(p.id)}/reviews`, {
          username: estado.usuario,
          calificacion: Number(datos.get("calificacion")),
          comentario: String(datos.get("comentario") || "").trim(),
        });
        ev.target.reset();
        aviso("Reseña publicada");
        pintarResenas();
      } catch (e) {
        aviso(e.message, "error");
      }
    });
  });
}

// ── Búsqueda ──────────────────────────────────────────────────────────

export async function buscar(main, { query }) {
  const q = (query.get("q") || "").trim();
  if (!q) {
    main.innerHTML = '<div class="vacio"><p class="vacio__titulo">Escriba qué busca en la barra de arriba.</p></div>';
    return;
  }
  main.innerHTML = cargando(`Buscando “${q}”`);
  try {
    await Promise.all([cargarCatalogo(), estado.promo ? null : cargarPromo()]);
    const { resultados } = await api.get(`/search?q=${enc(q)}`);
    // Elasticsearch guarda una copia de los textos; el precio y el stock
    // vigentes salen del catálogo (DynamoDB es la fuente de verdad).
    const productos = resultados
      .map((r) => estado.catalogo.get(r.productId) || { ...r, id: r.productId })
      .filter(Boolean);
    main.innerHTML = `
      <section aria-labelledby="busq-titulo">
        <h1 id="busq-titulo" class="seccion-titulo">Resultados para “${esc(q)}”</h1>
        <p class="consulta">Elasticsearch, con tolerancia a errores de escritura. ${productos.length} ${productos.length === 1 ? "resultado" : "resultados"}.</p>
        ${rejilla(productos, "Nada coincide. Pruebe con otra palabra o con la marca.")}
      </section>`;
  } catch (e) {
    main.innerHTML = bloqueError(e);
  }
}

// ── Carrito y checkout ────────────────────────────────────────────────

export async function carrito(main, { recargar }) {
  main.innerHTML = cargando("Cargando el carrito");
  let carritoApi;
  let direcciones = [];
  try {
    await (estado.promo ? null : cargarPromo());
    [carritoApi, direcciones] = await Promise.all([
      api.get("/cart", { usuario: estado.usuario }),
      api.get(`/users/${u()}/addresses`).then((d) => d.direcciones).catch(() => []),
    ]);
  } catch (e) {
    main.innerHTML = bloqueError(e);
    return;
  }

  if (!carritoApi.items.length) {
    main.innerHTML = `<div class="vacio">
      <p class="vacio__titulo">Tu carrito está vacío</p>
      <a class="boton" href="#/">Ver el catálogo</a></div>`;
    return;
  }

  // El carrito solo guarda productId y cantidad: nombre y precio salen del
  // detalle de cada producto (#1), que casi siempre viene de Redis.
  const detalles = await Promise.all(
    carritoApi.items.map((i) => api.get(`/products/${enc(i.productId)}`).catch(() => null)),
  );
  const lineas = carritoApi.items.map((i, n) => ({ ...i, producto: detalles[n] }));

  let cupon = null;   // cupón validado

  function totales() {
    const subtotal = lineas.reduce((s, l) => s + (l.producto ? precioVigente(l.producto).precio * l.cantidad : 0), 0);
    let descuento = 0;
    let avisoCupon = "";
    if (cupon) {
      if (cupon.monto_minimo && subtotal < cupon.monto_minimo) {
        avisoCupon = `Compra mínima de ${precio(cupon.monto_minimo)} para usar ${cupon.codigo}.`;
      } else {
        descuento = Math.round(subtotal * cupon.descuento_pct) / 100;
      }
    }
    return { subtotal, descuento, total: subtotal - descuento, avisoCupon };
  }

  function pintar() {
    const t = totales();
    main.innerHTML = `
      <h1 class="seccion-titulo">Carrito de ${esc(estado.usuario)}</h1>
      <div class="carrito">
        <ul class="lineas">${lineas.map((l) => {
          const p = l.producto;
          if (!p) {
            return `<li class="linea linea--rota"><span>${esc(l.productId)} ya no existe.</span>
              <button class="enlace" data-quitar="${esc(l.productId)}">Quitar</button></li>`;
          }
          const { precio: pv, enPromo } = precioVigente(p);
          return `<li class="linea">
            <span class="linea__arte">${icono(p.categoria)}</span>
            <span class="linea__info">
              <a href="#/producto/${enc(p.id)}" class="linea__nombre">${esc(p.nombre)}</a>
              <span class="linea__unidad">${precio(pv)} c/u${enPromo ? ", producto del día" : ""}</span>
            </span>
            <span class="paso" role="group" aria-label="Cantidad de ${esc(p.nombre)}">
              <button type="button" data-cambiar="${esc(p.id)}" data-delta="-1" aria-label="Quitar una" ${l.cantidad <= 1 ? "disabled" : ""}>−</button>
              <output>${l.cantidad}</output>
              <button type="button" data-cambiar="${esc(p.id)}" data-delta="1" aria-label="Agregar una" ${l.cantidad >= Math.min(99, p.stock) ? "disabled" : ""}>+</button>
            </span>
            <span class="linea__total">${precio(pv * l.cantidad)}</span>
            <button type="button" class="enlace" data-quitar="${esc(p.id)}">Quitar</button>
          </li>`;
        }).join("")}</ul>

        <form class="resumen" id="form-pago">
          <h2 class="subtitulo">Resumen</h2>
          <div class="cupon">
            <label class="campo">Código de descuento
              <span class="campo__fila">
                <input name="cupon" autocomplete="off" value="${esc(cupon?.codigo || "")}" placeholder="TECH10">
                <button class="boton boton--borde" type="button" id="aplicar-cupon">Aplicar</button>
              </span>
            </label>
            <p class="cupon__estado" id="cupon-estado" aria-live="polite">${
              cupon ? `${esc(cupon.codigo)}: ${esc(cupon.descuento_pct)}% de descuento` : ""}${
              t.avisoCupon ? `<br><span class="texto-error">${esc(t.avisoCupon)}</span>` : ""}</p>
          </div>
          <label class="campo">Enviar a
            <select name="direccion">
              ${direcciones.length
                ? direcciones.map((d) => `<option value="${esc(d.nombre)}">${esc(d.nombre)}: ${esc(d.direccion)}, ${esc(d.ciudad)}</option>`).join("")
                : '<option value="">Sin direcciones guardadas</option>'}
            </select>
          </label>
          ${direcciones.length ? "" : '<p class="nota"><a href="#/cuenta">Agregar una dirección</a></p>'}
          <dl class="cuentas">
            <dt>Subtotal</dt><dd>${precio(t.subtotal)}</dd>
            ${t.descuento ? `<dt>Cupón</dt><dd>−${precio(t.descuento)}</dd>` : ""}
            <dt class="cuentas__total">Total</dt><dd class="cuentas__total">${precio(t.total)}</dd>
          </dl>
          <button class="boton boton--ancho" type="submit">Pagar ${precio(t.total)}</button>
          <p class="nota">Al pagar, una sola transacción crea la orden, vacía el carrito y descuenta el stock.
            Si algo cambió, no se aplica nada.</p>
        </form>
      </div>`;
  }

  pintar();

  main.addEventListener("click", async (ev) => {
    const cambiar = ev.target.closest("[data-cambiar]");
    const quitar = ev.target.closest("[data-quitar]");
    if (cambiar) {
      const linea = lineas.find((l) => l.productId === cambiar.dataset.cambiar);
      const nueva = linea.cantidad + Number(cambiar.dataset.delta);
      try {
        await api.put(`/cart/${enc(linea.productId)}`, { cantidad: nueva }, { usuario: estado.usuario });
        linea.cantidad = nueva;
        pintar();
        refrescarCarrito();
      } catch (e) {
        aviso(e.message, "error");
      }
    } else if (quitar) {
      try {
        await api.del(`/cart/${enc(quitar.dataset.quitar)}`, { usuario: estado.usuario });
        lineas.splice(lineas.findIndex((l) => l.productId === quitar.dataset.quitar), 1);
        refrescarCarrito();
        if (lineas.length) pintar(); else recargar();
      } catch (e) {
        aviso(e.message, "error");
      }
    } else if (ev.target.id === "aplicar-cupon") {
      const codigo = main.querySelector("[name=cupon]").value.trim();
      if (!codigo) { cupon = null; pintar(); return; }
      try {
        const c = await api.get(`/coupons/${enc(codigo)}`);
        if (c.valido) {
          cupon = c;
          pintar();
        } else {
          cupon = null;
          pintar();
          main.querySelector("#cupon-estado").innerHTML = `<span class="texto-error">${esc(c.motivo)}</span>`;
        }
      } catch (e) {
        cupon = null;
        pintar();
        main.querySelector("#cupon-estado").innerHTML =
          `<span class="texto-error">${esc(e.status === 404 ? `El código ${codigo.toUpperCase()} no existe` : e.message)}</span>`;
      }
    }
  });

  // Enter en el campo del cupón aplica el cupón; no debe enviar el pago
  main.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.name === "cupon") {
      ev.preventDefault();
      main.querySelector("#aplicar-cupon").click();
    }
  });

  main.addEventListener("submit", async (ev) => {
    if (ev.target.id !== "form-pago") return;
    ev.preventDefault();
    const datos = new FormData(ev.target);
    const body = {};
    if (cupon && !totales().avisoCupon) body.cupon = cupon.codigo;
    if (datos.get("direccion")) body.direccion = datos.get("direccion");
    await conBoton(ev.submitter, async () => {
      try {
        const orden = await api.post("/orders", body, { usuario: estado.usuario });
        refrescarCarrito();
        location.hash = `#/pedido/${enc(orden.orderId)}?nueva=1`;
      } catch (e) {
        aviso(e.message, "error");
        if (e instanceof ErrorApi && e.status === 409) recargar();
      }
    });
  });
}

// ── Pedidos ───────────────────────────────────────────────────────────

export async function pedidos(main) {
  main.innerHTML = cargando("Cargando los pedidos");
  try {
    const [historial, ultima] = await Promise.all([
      api.get(`/users/${u()}/orders`),
      api.get(`/users/${u()}/orders/ultima`).catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    ]);
    if (!historial.ordenes.length) {
      main.innerHTML = `<div class="vacio"><p class="vacio__titulo">${esc(estado.usuario)} todavía no tiene pedidos</p>
        <a class="boton" href="#/">Ver el catálogo</a></div>`;
      return;
    }
    main.innerHTML = `
      <h1 class="seccion-titulo">Pedidos de ${esc(estado.usuario)}</h1>
      ${ultima ? `<a class="ultimo" href="#/pedido/${enc(ultima.orderId)}">
        <span class="ultimo__titulo">Tu último pedido</span>
        <span class="ultimo__detalle">${esc(fecha(ultima.fecha))}: ${ultima.items.map((i) => esc(i.nombre)).join(", ")}</span>
        <span class="ultimo__total">${precio(ultima.total)}</span>
      </a>` : ""}
      <table class="tabla">
        <caption class="nota">Del más reciente al más antiguo: la fecha va al inicio de la llave de ordenamiento.</caption>
        <thead><tr><th scope="col">Fecha</th><th scope="col">Número</th><th scope="col">Unidades</th><th scope="col" class="num">Total</th></tr></thead>
        <tbody>${historial.ordenes.map((o) => `<tr>
          <td>${esc(fecha(o.fecha))}</td>
          <td><a href="#/pedido/${enc(o.orderId)}">${esc(o.orderId)}</a></td>
          <td>${esc(o.total_unidades)}</td>
          <td class="num">${precio(o.total)}</td></tr>`).join("")}</tbody>
      </table>`;
  } catch (e) {
    main.innerHTML = bloqueError(e);
  }
}

export async function pedido(main, { params, query }) {
  main.innerHTML = cargando("Cargando el pedido");
  try {
    const o = await api.get(`/orders/${enc(params.id)}`);
    const d = o.direccion;
    main.innerHTML = `
      ${query.get("nueva") ? `<div class="confirmacion" role="status">
        <p class="confirmacion__titulo">Compra confirmada</p>
        <p>Guardamos el pedido ${esc(o.orderId)} y vaciamos tu carrito.</p></div>` : ""}
      <article class="orden">
        <header class="orden__cabeza">
          <h1 class="seccion-titulo">Pedido ${esc(o.orderId)}</h1>
          <p>${esc(fecha(o.fecha))}, de ${esc(o.username)}. Estado: ${esc(o.estado.toLowerCase())}.</p>
          ${d ? `<p>Envío a ${esc(d.nombre)}: ${esc(d.direccion)}, ${esc(d.ciudad)}.</p>` : ""}
        </header>
        <table class="tabla">
          <thead><tr><th scope="col">Producto</th><th scope="col" class="num">Precio</th><th scope="col" class="num">Cantidad</th><th scope="col" class="num">Subtotal</th></tr></thead>
          <tbody>${o.items.map((i) => `<tr>
            <td><a href="#/producto/${enc(i.productId)}">${esc(i.nombre)}</a>${i.promocion ? ' <span class="chip chip--sol">Producto del día</span>' : ""}</td>
            <td class="num">${precio(i.precio_unitario)}${i.precio_lista ? `<br><s class="precio-antes">${precio(i.precio_lista)}</s>` : ""}</td>
            <td class="num">${esc(i.cantidad)}</td>
            <td class="num">${precio(i.subtotal)}</td></tr>`).join("")}</tbody>
          <tfoot>
            <tr><th scope="row" colspan="3">Subtotal</th><td class="num">${precio(o.subtotal)}</td></tr>
            ${o.descuento ? `<tr><th scope="row" colspan="3">Cupón ${esc(o.cupon)}</th><td class="num">−${precio(o.descuento)}</td></tr>` : ""}
            <tr class="tabla__total"><th scope="row" colspan="3">Total pagado</th><td class="num">${precio(o.total)}</td></tr>
          </tfoot>
        </table>
        <p class="nota">Consultado por número en el índice GSI1, sin saber de quién es. Llegó de ${fuente(o._fuente)};
          una orden pagada no cambia, por eso se puede guardar en caché.</p>
        <p><a class="boton boton--borde" href="#/pedidos">Ver todos los pedidos</a></p>
      </article>`;
  } catch (e) {
    main.innerHTML = bloqueError(
      e.status === 404 ? `No existe un pedido con el número ${params.id}.` : e,
      '<a class="boton" href="#/pedidos">Ver mis pedidos</a>',
    );
  }
}

// ── Mi cuenta ─────────────────────────────────────────────────────────

export async function cuenta(main, { recargar }) {
  main.innerHTML = cargando("Cargando la cuenta");
  let perfil = null;
  try {
    perfil = await api.get(`/users/${u()}`);
  } catch (e) {
    if (e.status !== 404) { main.innerHTML = bloqueError(e); return; }
  }

  const formNueva = `
    <form class="panel" id="form-usuario">
      <h2 class="subtitulo">Crear una cuenta</h2>
      <label class="campo">Usuario
        <input name="username" required pattern="[A-Za-z0-9_.\\-]{3,30}" value="${perfil ? "" : esc(estado.usuario)}"
          aria-describedby="ayuda-usuario">
      </label>
      <p class="nota" id="ayuda-usuario">De 3 a 30 letras, números, punto, guion o guion bajo.</p>
      <label class="campo">Nombre <input name="nombre" required></label>
      <label class="campo">Correo <input name="email" type="email"></label>
      <button class="boton" type="submit">Crear cuenta</button>
    </form>`;

  if (!perfil) {
    main.innerHTML = `<div class="vacio"><p class="vacio__titulo">La cuenta ${esc(estado.usuario)} no existe todavía</p>
      <p>Créela para guardar direcciones y favoritos.</p></div>${formNueva}`;
    enlazarFormUsuario();
    return;
  }

  main.innerHTML = `
    <h1 class="seccion-titulo">${esc(perfil.nombre)}</h1>
    <p class="perfil">${esc(perfil.username)}${perfil.email ? `, ${esc(perfil.email)}` : ""}. Nivel ${esc(perfil.nivel)}.</p>
    <div class="cuenta">
      <section aria-labelledby="dir-titulo">
        <h2 id="dir-titulo" class="subtitulo">Direcciones</h2>
        <div id="direcciones">${cargando()}</div>
        <form class="panel" id="form-direccion">
          <h3 class="subtitulo-chico">Agregar o actualizar una dirección</h3>
          <label class="campo">Nombre <input name="nombre" required placeholder="Casa, Oficina…"></label>
          <label class="campo">Dirección <input name="direccion" required></label>
          <label class="campo">Ciudad <input name="ciudad" required></label>
          <label class="campo">Teléfono <input name="telefono" type="tel"></label>
          <button class="boton" type="submit">Guardar dirección</button>
        </form>
      </section>
      <section aria-labelledby="fav-titulo">
        <h2 id="fav-titulo" class="subtitulo">Favoritos</h2>
        <div id="favoritos">${cargando()}</div>
      </section>
    </div>
    <details class="otra-cuenta"><summary>Crear otra cuenta</summary>${formNueva}</details>`;

  async function pintarDirecciones() {
    const zona = main.querySelector("#direcciones");
    const { direcciones } = await api.get(`/users/${u()}/addresses`);
    zona.innerHTML = direcciones.length
      ? `<ul class="direcciones">${direcciones.map((d) => `<li>
          <strong>${esc(d.nombre)}</strong> ${esc(d.direccion)}, ${esc(d.ciudad)}${d.telefono ? `. Tel. ${esc(d.telefono)}` : ""}
          <button class="enlace" data-borrar-dir="${esc(d.nombre)}">Borrar</button></li>`).join("")}</ul>`
      : '<p class="nota">Sin direcciones guardadas.</p>';
  }

  async function pintarFavoritos() {
    const zona = main.querySelector("#favoritos");
    await cargarCatalogo();
    const { productos } = await api.get(`/users/${u()}/wishlist`);
    const lista = productos.map((w) => estado.catalogo.get(w.productId)).filter(Boolean);
    zona.innerHTML = lista.length
      ? `<ul class="favoritos">${lista.map((p) => `<li>
          <a href="#/producto/${enc(p.id)}">${esc(p.nombre)}</a> <span>${precio(precioVigente(p).precio)}</span>
          <button class="enlace" data-quitar-fav="${esc(p.id)}">Quitar</button></li>`).join("")}</ul>`
      : '<p class="nota">Use “Guardar en favoritos” en la ficha de un producto.</p>';
  }

  await Promise.all([pintarDirecciones(), pintarFavoritos()]).catch((e) => aviso(e.message, "error"));

  main.querySelector("#form-direccion").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const d = Object.fromEntries(new FormData(ev.target));
    await conBoton(ev.submitter, async () => {
      try {
        await api.put(`/users/${u()}/addresses/${enc(d.nombre.trim())}`, {
          direccion: d.direccion, ciudad: d.ciudad, telefono: d.telefono,
        });
        ev.target.reset();
        aviso("Dirección guardada");
        pintarDirecciones();
      } catch (e) {
        aviso(e.message, "error");
      }
    });
  });

  main.addEventListener("click", async (ev) => {
    const dir = ev.target.closest("[data-borrar-dir]");
    const fav = ev.target.closest("[data-quitar-fav]");
    try {
      if (dir) {
        await api.del(`/users/${u()}/addresses/${enc(dir.dataset.borrarDir)}`);
        aviso("Dirección borrada");
        pintarDirecciones();
      } else if (fav) {
        await api.del(`/users/${u()}/wishlist/${enc(fav.dataset.quitarFav)}`);
        aviso("Quitado de favoritos");
        pintarFavoritos();
      }
    } catch (e) {
      aviso(e.message, "error");
    }
  });

  enlazarFormUsuario();

  function enlazarFormUsuario() {
    main.querySelector("#form-usuario").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const d = Object.fromEntries(new FormData(ev.target));
      await conBoton(ev.submitter, async () => {
        try {
          await api.post("/users", d);
          aviso(`Cuenta ${d.username} creada`);
          cambiarUsuario(d.username);
          recargar();
        } catch (e) {
          aviso(e.message, "error");
        }
      });
    });
  }
}

// ── Gestión (administración para la demo) ─────────────────────────────

export async function gestion(main) {
  main.innerHTML = cargando("Cargando");
  try {
    await Promise.all([cargarCatalogo(true), cargarPromo()]);
  } catch (e) {
    main.innerHTML = bloqueError(e);
    return;
  }
  const productos = [...estado.catalogo.values()].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  const opciones = (sel) => productos.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.nombre)}</option>`).join("");
  const promo = estado.promo?.productos || [];

  main.innerHTML = `
    <h1 class="seccion-titulo">Gestión</h1>
    <section class="salud" aria-labelledby="salud-titulo">
      <h2 id="salud-titulo" class="subtitulo">Servicios</h2>
      <div id="salud">${cargando()}</div>
    </section>
    <div class="gestion">
      <form class="panel" id="form-promo">
        <h2 class="subtitulo">Producto del día</h2>
        <p class="nota">Se guarda en DynamoDB y en el mismo instante en Redis, hasta la medianoche.</p>
        <label class="campo">Primer producto <select name="p1">${opciones(promo[0]?.productId)}</select></label>
        <label class="campo">Segundo producto <select name="p2">${opciones(promo[1]?.productId)}</select></label>
        <label class="campo">Descuento (%) <input name="pct" type="number" min="1" max="100" value="${esc(estado.promo?.descuento_pct || 30)}" required></label>
        <button class="boton" type="submit">Publicar promoción</button>
      </form>

      <form class="panel" id="form-cupon">
        <h2 class="subtitulo">Cupón</h2>
        <label class="campo">Código <input name="codigo" required pattern="[A-Za-z0-9_\\-]{3,20}"></label>
        <label class="campo">Descuento (%) <input name="descuento_pct" type="number" min="1" max="100" value="10" required></label>
        <label class="campo">Válido desde <input name="vigente_desde" type="date"></label>
        <label class="campo">Válido hasta <input name="vigente_hasta" type="date"></label>
        <label class="campo">Compra mínima (COP) <input name="monto_minimo" type="number" min="0" step="1000"></label>
        <label class="campo-check"><input name="activo" type="checkbox" checked> Activo</label>
        <button class="boton" type="submit">Guardar cupón</button>
      </form>

      <form class="panel" id="form-producto">
        <h2 class="subtitulo">Producto</h2>
        <label class="campo">Código <input name="id" required placeholder="APPL-MBA-M3"></label>
        <label class="campo">Nombre <input name="nombre" required></label>
        <label class="campo">Categoría <input name="categoria" required list="lista-categorias"></label>
        <datalist id="lista-categorias">${[...new Set(productos.map((p) => p.categoria))].map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
        <label class="campo">Marca <input name="marca" required></label>
        <label class="campo">Precio (COP) <input name="precio" type="number" min="0" step="1000" required></label>
        <label class="campo">Stock <input name="stock" type="number" min="0" value="10" required></label>
        <label class="campo">Descripción <textarea name="descripcion" rows="2"></textarea></label>
        <button class="boton" type="submit">Guardar producto</button>
      </form>

      <section class="panel">
        <h2 class="subtitulo">Índice de búsqueda</h2>
        <p class="nota">Vuelve a copiar todos los productos de DynamoDB a Elasticsearch.
          Úselo si se cargaron datos sin pasar por el API o si Elasticsearch estuvo caído.</p>
        <button class="boton boton--borde" type="button" id="reindexar">Reconstruir índice</button>
        <p class="nota" id="reindex-estado" aria-live="polite"></p>
      </section>
    </div>`;

  pintarSalud(main.querySelector("#salud"));

  const enviar = (selector, fn) => main.querySelector(selector).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const datos = Object.fromEntries(new FormData(ev.target));
    await conBoton(ev.submitter, async () => {
      try { await fn(datos, ev.target); } catch (e) { aviso(e.message, "error"); }
    });
  });

  enviar("#form-promo", async (d) => {
    const r = await api.post("/promotions", { productos: [d.p1, d.p2], descuento_pct: Number(d.pct) });
    estado.promo = null;
    aviso(r._en_cache ? "Promoción publicada y guardada en Redis" : "Promoción publicada (Redis no respondió)");
  });

  enviar("#form-cupon", async (d, form) => {
    const body = { codigo: d.codigo, descuento_pct: Number(d.descuento_pct), activo: form.activo.checked };
    if (d.vigente_desde) body.vigente_desde = d.vigente_desde;
    if (d.vigente_hasta) body.vigente_hasta = d.vigente_hasta;
    if (d.monto_minimo) body.monto_minimo = Number(d.monto_minimo);
    const c = await api.post("/coupons", body);
    aviso(`Cupón ${c.codigo} guardado${c.valido ? "" : `: ${c.motivo}`}`);
  });

  enviar("#form-producto", async (d, form) => {
    const p = await api.post("/products", { ...d, precio: Number(d.precio), stock: Number(d.stock) });
    form.reset();
    await cargarCatalogo(true);
    aviso(p._indexado_busqueda ? `${p.nombre} guardado y listo para buscar` : `${p.nombre} guardado (sin indexar en la búsqueda)`);
  });

  main.querySelector("#reindexar").addEventListener("click", async (ev) => {
    const salida = main.querySelector("#reindex-estado");
    await conBoton(ev.target, async () => {
      try {
        const r = await api.post("/search/reindex");
        salida.textContent = `${r.indexados} de ${r.productos_en_dynamodb} productos indexados.`;
      } catch (e) {
        salida.textContent = e.message;
      }
    });
  });
}

export async function pintarSalud(zona) {
  let datos;
  try {
    datos = await api.get("/health");
  } catch (e) {
    datos = e.cuerpo || {};
  }
  const nombres = { dynamodb: "DynamoDB", redis: "Redis", elasticsearch: "Elasticsearch" };
  zona.innerHTML = `<ul class="servicios">${Object.entries(nombres).map(([clave, nombre]) => {
    const s = datos[clave] || { estado: "error", detalle: "Sin respuesta" };
    return `<li class="servicio servicio--${s.estado === "ok" ? "ok" : "error"}">
      <span class="servicio__nombre">${nombre}</span>
      <span>${s.estado === "ok" ? "En línea" : esc(s.detalle || "Sin respuesta")}</span></li>`;
  }).join("")}</ul>`;
}
