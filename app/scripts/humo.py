"""
humo.py
───────
Prueba de humo de punta a punta contra el API desplegado en el emulador:
recorre todas las rutas (catálogo, búsqueda, usuarios, cupones, promoción,
carrito, checkout y órdenes) con DynamoDB, Redis y Elasticsearch reales.
Crea sus propios datos con un sufijo aleatorio, así no choca con el seed.

Uso (después de `cdklocal deploy`):
  docker compose exec cdk-dev python scripts/humo.py            # api-id automático
  python app/scripts/humo.py <api-id>                           # desde el host

Sale con código 1 si alguna comprobación falla.
"""
import sys
import time
import uuid

from api_local import Api

api = Api(sys.argv[1] if len(sys.argv) > 1 else None)
pedir = api.pedir
print(f"API: {api.base}")
print()

S = uuid.uuid4().hex[:6].upper()          # sufijo único por corrida
U = f"humo_{S.lower()}"
fallos: list[str] = []


def ok(nombre, condicion, detalle=""):
    print(("PASS " if condicion else "FAIL ") + nombre + ("" if condicion else f"  -> {detalle}"))
    if not condicion:
        fallos.append(nombre)


# ── Health ────────────────────────────────────────────────────────────
st, b = pedir("GET", "/health")
ok("health 200 con dynamodb+redis+elasticsearch ok", st == 200 and all(
    b.get(c, {}).get("estado") == "ok" for c in ("dynamodb", "redis", "elasticsearch")), (st, b))

# ── Productos ─────────────────────────────────────────────────────────
A, B, C = f"MBA-{S}", f"WH-{S}", f"UW-{S}"
cat, cat2 = f"Laptops{S}", f"Audio{S}"
for pid, nombre, categoria, marca, precio, stock in [
    (A, "MacBook Air M3", cat, f"Apple{S}", 1000, 5),
    (B, "Audifonos WH-1000XM5", cat2, f"Sony{S}", 300, 10),
    (C, "Monitor ultrawide", cat, f"LG{S}", 400, 2),
]:
    st, b = pedir("POST", "/products", {"id": pid, "nombre": nombre, "categoria": categoria,
                                        "marca": marca, "precio": precio, "stock": stock,
                                        "descripcion": f"Descripcion de {nombre}"})
    ok(f"POST /products {pid} 201 e indexado", st == 201 and b.get("_indexado_busqueda"), (st, b))

st, b1 = pedir("GET", f"/products/{A}")
st2, b2 = pedir("GET", f"/products/{A}")
ok("GET /products/{id} miss→dynamodb, luego cache",
   (st, b1.get("_fuente"), st2, b2.get("_fuente")) == (200, "dynamodb", 200, "cache"), (b1, b2))
ok("GET /products/{id} inexistente 404", pedir("GET", "/products/NOPE-" + S)[0] == 404)

st, b = pedir("GET", f"/categories/{cat}/products")
ok("GET /categories/{cat}/products (GSI1)", st == 200 and sorted(
    p["id"] for p in b["productos"]) == sorted([A, C]), (st, b))
st, b = pedir("GET", f"/brands/Apple{S}/products")
ok("GET /brands/{marca}/products (GSI2)", st == 200 and [p["id"] for p in b["productos"]] == [A], b)
st, b = pedir("GET", f"/brands/Apple{S}/products?categoria={cat2}")
ok("GET /brands/{marca}/products?categoria= filtra", st == 200 and b["productos"] == [], b)
st, b = pedir("GET", "/products")
ok("GET /products (Scan) incluye los 3", st == 200 and {A, B, C} <= {p["id"] for p in b["productos"]}, st)

for usuario, cal in (("ana", 5), ("beto", 3)):
    st, _ = pedir("POST", f"/products/{A}/reviews", {"username": usuario, "calificacion": cal,
                                                       "comentario": "ok"})
    ok(f"POST reseña de {usuario} 201", st == 201, st)
    time.sleep(0.05)
st, b = pedir("GET", f"/products/{A}/reviews")
ok("GET reseñas, más reciente primero", st == 200 and [r["username"] for r in b["resenas"]] ==
   ["beto", "ana"], b)

st, b = pedir("GET", "/products/ranking?limite=50")
vistas = {r["productId"]: r["vistas"] for r in b.get("ranking", [])}
ok("GET /products/ranking (Sorted Set) cuenta las 2 vistas de A", st == 200 and vistas.get(A) == 2,
   (st, b))

# ── Búsqueda ──────────────────────────────────────────────────────────
time.sleep(1.5)   # refresh de Elasticsearch (1 s por defecto)
st, b = pedir("GET", "/search?q=macbok")
ok("GET /search?q=macbok (typo) encuentra A", st == 200 and A in [r["productId"] for r in
                                                                   b["resultados"]], (st, b))
ok("GET /search sin q 400", pedir("GET", "/search")[0] == 400)
st, b = pedir("POST", "/search/reindex")
ok("POST /search/reindex", st == 200 and b["indexados"] == b["productos_en_dynamodb"] >= 3, (st, b))
st, b = pedir("GET", "/search?q=ultrawide")
ok("búsqueda tras reindex", st == 200 and C in [r["productId"] for r in b["resultados"]], (st, b))

# ── Usuarios ──────────────────────────────────────────────────────────
st, b = pedir("POST", "/users", {"username": U, "nombre": "Humo", "email": "humo@example.com"})
ok("POST /users 201", st == 201 and b["nivel"] == "bronce", (st, b))
ok("POST /users repetido 409", pedir("POST", "/users", {"username": U, "nombre": "X"})[0] == 409)
st, b = pedir("PUT", f"/users/{U}/addresses/Casa", {"direccion": "Calle 1 #2-3",
                                                     "ciudad": "Santa Marta"})
ok("PUT dirección 200", st == 200, (st, b))
ok("PUT wishlist 200", pedir("PUT", f"/users/{U}/wishlist/{C}")[0] == 200)
st, b = pedir("GET", f"/users/{U}")
ok("GET /users/{u} perfil", st == 200 and b["nombre"] == "Humo", (st, b))
st, b = pedir("GET", f"/users/{U}/addresses")
ok("GET direcciones", st == 200 and [d["nombre"] for d in b["direcciones"]] == ["Casa"], b)
st, b = pedir("GET", f"/users/{U}/wishlist")
ok("GET wishlist", st == 200 and [p["productId"] for p in b["productos"]] == [C], b)

# ── Cupones ───────────────────────────────────────────────────────────
cupon = f"HUMO{S}"
st, b = pedir("POST", "/coupons", {"codigo": cupon, "descuento_pct": 10})
ok("POST /coupons 201", st == 201 and b["valido"], (st, b))
st, b = pedir("GET", f"/coupons/{cupon.lower()}")
ok("GET /coupons/{codigo} válido (sin distinguir mayúsculas)", st == 200 and b["valido"], (st, b))
ok("GET /coupons inexistente 404", pedir("GET", f"/coupons/NOPE{S}")[0] == 404)

# ── Promoción del día ─────────────────────────────────────────────────
# La prueba reemplaza la promoción de hoy: se guarda la vigente para
# restaurarla al final y no dañar los datos de la demo (seed).
st_previa, promo_previa = pedir("GET", "/promotions/today")
st, b = pedir("POST", "/promotions", {"productos": [A, B], "descuento_pct": 50})
ok("POST /promotions 201 y en caché (Write-Through)", st == 201 and b["_en_cache"], (st, b))
st, b = pedir("GET", "/promotions/today")
ok("GET /promotions/today sale de cache", st == 200 and b["_fuente"] == "cache"
   and b["descuento_pct"] == 50, (st, b))

# ── Carrito ───────────────────────────────────────────────────────────
ok("GET /cart sin usuario 400", pedir("GET", "/cart")[0] == 400)
ok("PUT /cart A x2", pedir("PUT", f"/cart/{A}", {"cantidad": 2}, usuario=U)[0] == 200)
ok("PUT /cart C x1", pedir("PUT", f"/cart/{C}", {"cantidad": 1}, usuario=U)[0] == 200)
st, b = pedir("GET", "/cart", usuario=U)
ok("GET /cart", st == 200 and b["total_unidades"] == 3, (st, b))

# ── Checkout y órdenes ────────────────────────────────────────────────
st, orden = pedir("POST", "/orders", {"cupon": cupon, "direccion": "Casa"}, usuario=U)
# A en promo 50%: 500 x2 = 1000 ; C sin promo: 400 → subtotal 1400, cupón 10% → 1260
ok("POST /orders 201 con promo + cupón + dirección", st == 201 and
   (orden.get("subtotal"), orden.get("descuento"), orden.get("total")) == (1400, 140, 1260)
   and orden.get("direccion", {}).get("ciudad") == "Santa Marta", (st, orden))
oid = orden.get("orderId", "")
st, b = pedir("GET", "/cart", usuario=U)
ok("carrito vacío tras el checkout", st == 200 and b["items"] == [], b)
st, b = pedir("GET", f"/products/{A}")
ok("stock de A descontado (5→3) y caché invalidada", st == 200 and b["stock"] == 3
   and b["_fuente"] == "dynamodb", b)
ok("POST /orders con carrito vacío 400", pedir("POST", "/orders", {}, usuario=U)[0] == 400)

pedir("PUT", f"/cart/{C}", {"cantidad": 5}, usuario=U)
st, b = pedir("POST", "/orders", {}, usuario=U)
ok("POST /orders sin stock 409", st == 409, (st, b))
pedir("DELETE", f"/cart/{C}", usuario=U)

st, b = pedir("GET", f"/users/{U}/orders")
ok("GET historial", st == 200 and [o["orderId"] for o in b["ordenes"]] == [oid], b)
st, b = pedir("GET", f"/users/{U}/orders/ultima")
ok("GET última orden con sus líneas", st == 200 and b.get("orderId") == oid
   and len(b.get("items", [])) == 2, (st, b))
st, b1 = pedir("GET", f"/orders/{oid}")
st2, b2 = pedir("GET", f"/orders/{oid}")
ok("GET /orders/{id} (GSI1) miss→dynamodb, luego cache",
   (st, b1.get("_fuente"), st2, b2.get("_fuente")) == (200, "dynamodb", 200, "cache"), (b1, b2))

# ── Borrados ──────────────────────────────────────────────────────────
ok("DELETE /cart de producto ausente 404", pedir("DELETE", f"/cart/{A}", usuario=U)[0] == 404)
ok("DELETE wishlist", pedir("DELETE", f"/users/{U}/wishlist/{C}")[0] == 200)
ok("DELETE dirección", pedir("DELETE", f"/users/{U}/addresses/Casa")[0] == 200)
st, b = pedir("DELETE", f"/products/{B}")
ok("DELETE /products/{id}", st == 200, (st, b))
ok("producto borrado → 404", pedir("GET", f"/products/{B}")[0] == 404)
time.sleep(1.5)
st, b = pedir("GET", "/search?q=audifonos")
ok("producto borrado ya no aparece en la búsqueda", st == 200 and B not in [
    r["productId"] for r in b["resultados"]], b)

# ── Limpieza: deja la tienda como estaba ───────────────────────────────
# Los productos de prueba crean categorías y marcas con sufijo que se verían
# en el catálogo. (Usuario, cupón y orden de prueba no tienen ruta de borrado;
# no aparecen en el catálogo ni en las cuentas del seed.)
pedir("DELETE", f"/products/{A}")
pedir("DELETE", f"/products/{C}")
if st_previa == 200:
    pedir("POST", "/promotions", {
        "productos": [p["productId"] for p in promo_previa["productos"]],
        "descuento_pct": promo_previa["descuento_pct"],
    })
st, b = pedir("GET", "/products")
ok("limpieza: no quedan productos de esta corrida",
   st == 200 and not {A, B, C} & {p["id"] for p in b["productos"]}, st)

print(f"\n{'TODO OK' if not fallos else f'{len(fallos)} FALLOS: ' + ', '.join(fallos)}")
sys.exit(1 if fallos else 0)
