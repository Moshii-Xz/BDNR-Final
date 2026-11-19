"""
seed.py
───────
Carga datos de prueba para la demo, A TRAVÉS DEL API (no escribe directo en
DynamoDB), para que cada dato pase por el mismo camino que en producción:
  - los productos quedan indexados en Elasticsearch (búsqueda #6),
  - la promoción del día entra a Redis por Write-Through (#16/#17),
  - las órdenes pasan por el checkout transaccional (#12) y descuentan stock,
  - las visitas a fichas de producto alimentan el ranking (Sorted Set).

Se puede correr varias veces sobre el mismo deploy: productos, direcciones,
favoritos, cupones y promoción se reemplazan (PUT/POST idempotentes); usuarios
existentes, reseñas y órdenes ya creadas se saltan. LocalStack Community no
persiste datos: después de reiniciar el emulador hay que volver a desplegar y
volver a correr el seed.

Uso (después de `cdklocal deploy`):
  docker compose exec cdk-dev python scripts/seed.py        # api-id automático
  python app/scripts/seed.py <api-id>                       # desde el host
"""
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from api_local import Api

# Fechas relativas a hoy (Bogotá) para que los cupones sigan teniendo sentido
# el día de la sustentación, no solo el día en que se escribió el script.
HOY = datetime.now(timezone(timedelta(hours=-5))).date()


def dias(n: int) -> str:
    return (HOY + timedelta(days=n)).isoformat()


# ── Catálogo ──────────────────────────────────────────────────────────
# (id, nombre, categoria, marca, precio COP, stock, descripcion)
PRODUCTOS: list[tuple[str, str, str, str, int, int, str]] = [
    ("APPL-MBA-M3", "MacBook Air 13 M3", "Laptops", "Apple", 5899000, 8,
     "Portátil ultraliviano con chip M3, 16 GB de RAM y 512 GB SSD."),
    ("APPL-MBP-M4", "MacBook Pro 14 M4 Pro", "Laptops", "Apple", 10499000, 4,
     "Portátil profesional con pantalla Liquid Retina XDR y 24 GB de RAM."),
    ("LEN-TP-X1", "ThinkPad X1 Carbon Gen 12", "Laptops", "Lenovo", 7299000, 6,
     "Portátil empresarial de fibra de carbono, teclado resistente a derrames."),
    ("ASUS-ROG-G14", "ROG Zephyrus G14", "Laptops", "Asus", 8199000, 5,
     "Portátil gamer con RTX 4070 y pantalla OLED de 120 Hz."),
    ("SONY-WH1000", "Audífonos WH-1000XM5", "Audio", "Sony", 1499000, 15,
     "Audífonos inalámbricos con cancelación de ruido líder y 30 h de batería."),
    ("APPL-APP2", "AirPods Pro 2", "Audio", "Apple", 1199000, 20,
     "Audífonos in-ear con cancelación activa de ruido y estuche USB-C."),
    ("JBL-FLIP6", "Parlante JBL Flip 6", "Audio", "JBL", 549000, 25,
     "Parlante bluetooth portátil resistente al agua IP67."),
    ("LG-UW34", "Monitor LG UltraWide 34", "Monitores", "LG", 1899000, 7,
     "Monitor curvo ultrawide de 34 pulgadas, resolución WQHD y 160 Hz."),
    ("SAMS-ODY-G5", "Monitor Samsung Odyssey G5 27", "Monitores", "Samsung", 1099000, 10,
     "Monitor gamer curvo QHD de 27 pulgadas a 165 Hz."),
    ("DELL-U2724", "Monitor Dell UltraSharp 27", "Monitores", "Dell", 2299000, 5,
     "Monitor IPS 4K con USB-C para productividad y diseño."),
    ("APPL-IP16", "iPhone 16 128 GB", "Celulares", "Apple", 4599000, 12,
     "Celular con chip A18, cámara de 48 MP y botón de acción."),
    ("SAMS-S24", "Samsung Galaxy S24", "Celulares", "Samsung", 3899000, 14,
     "Celular con Galaxy AI, pantalla de 120 Hz y triple cámara."),
    ("XIAO-RN13", "Xiaomi Redmi Note 13 Pro", "Celulares", "Xiaomi", 1349000, 30,
     "Celular de gama media con cámara de 200 MP y carga rápida de 67 W."),
    ("LOGI-MX3S", "Mouse Logitech MX Master 3S", "Periféricos", "Logitech", 459000, 40,
     "Mouse ergonómico inalámbrico con clics silenciosos y scroll electromagnético."),
    ("LOGI-MXKEYS", "Teclado Logitech MX Keys S", "Periféricos", "Logitech", 529000, 30,
     "Teclado inalámbrico retroiluminado para varios dispositivos."),
    ("KEYC-K2", "Teclado mecánico Keychron K2", "Periféricos", "Keychron", 399000, 18,
     "Teclado mecánico compacto 75 % con switches intercambiables."),
    ("ANKR-PB20", "Power bank Anker 20000 mAh", "Accesorios", "Anker", 219000, 50,
     "Batería externa de 20000 mAh con carga rápida USB-C de 30 W."),
    ("SAMS-T7-1TB", "SSD externo Samsung T7 1 TB", "Accesorios", "Samsung", 489000, 22,
     "Disco sólido portátil USB 3.2 de hasta 1050 MB/s."),
]

# ── Usuarios, direcciones y favoritos ─────────────────────────────────
USUARIOS: list[dict[str, Any]] = [
    {"username": "camilo_dev", "nombre": "Camilo Jiménez", "email": "camilo@example.com",
     "direcciones": [
         ("Casa", {"direccion": "Calle 22 #4-35", "ciudad": "Santa Marta",
                   "telefono": "3001234567"}),
         ("Universidad", {"direccion": "Carrera 32 #22-08, Unimagdalena",
                          "ciudad": "Santa Marta"}),
     ],
     "favoritos": ["APPL-MBP-M4", "LG-UW34", "KEYC-K2"]},
    {"username": "caro_dev", "nombre": "Carolina Pérez", "email": "caro@example.com",
     "direcciones": [("Apartamento", {"direccion": "Calle 85 #11-53", "ciudad": "Bogotá",
                                      "telefono": "3109876543"})],
     "favoritos": ["SONY-WH1000", "APPL-IP16"]},
    {"username": "ana_compras", "nombre": "Ana María Ruiz", "email": "ana@example.com",
     "direcciones": [("Casa", {"direccion": "Carrera 70 #45-12", "ciudad": "Medellín"})],
     "favoritos": ["SAMS-S24"]},
    {"username": "beto_tech", "nombre": "Alberto Gómez", "email": "beto@example.com",
     "direcciones": [], "favoritos": []},
]

# ── Reseñas: (productId, username, calificacion, comentario) ──────────
RESENAS: list[tuple[str, str, int, str]] = [
    ("APPL-MBA-M3", "caro_dev", 5, "Liviano y la batería dura todo el día."),
    ("APPL-MBA-M3", "ana_compras", 4, "Excelente, aunque el precio es alto."),
    ("APPL-MBA-M3", "beto_tech", 5, "Perfecto para programar."),
    ("SONY-WH1000", "camilo_dev", 5, "La cancelación de ruido es increíble."),
    ("SONY-WH1000", "beto_tech", 4, "Muy cómodos, el estuche es grande."),
    ("LG-UW34", "camilo_dev", 5, "Cambió mi forma de trabajar con dos ventanas."),
    ("LOGI-MX3S", "caro_dev", 5, "El mejor mouse que he tenido."),
    ("XIAO-RN13", "ana_compras", 3, "Buena cámara, el software trae mucha publicidad."),
]

# ── Cupones (cubren los casos de validación de #8) ────────────────────
CUPONES: list[dict[str, Any]] = [
    {"codigo": "TECH10", "descuento_pct": 10, "vigente_hasta": dias(60)},            # válido
    {"codigo": "BIENVENIDA15", "descuento_pct": 15, "monto_minimo": 1000000},       # con mínimo
    {"codigo": "NAVIDAD25", "descuento_pct": 25, "vigente_desde": dias(10),
     "vigente_hasta": dias(40)},                                                    # aún no
    {"codigo": "VERANO5", "descuento_pct": 5, "vigente_desde": dias(-120),
     "vigente_hasta": dias(-30)},                                                   # vencido
    {"codigo": "PRUEBAOFF", "descuento_pct": 50, "activo": False},                  # desactivado
]

# ── Promoción del día ─────────────────────────────────────────────────
PROMOCION: dict[str, Any] = {"productos": ["SONY-WH1000", "LOGI-MX3S"], "descuento_pct": 30}

# ── Órdenes: (username, [(productId, cantidad)], cupon, direccion) ────
ORDENES: list[tuple[str, list[tuple[str, int]], str | None, str | None]] = [
    ("camilo_dev", [("APPL-MBA-M3", 1), ("LOGI-MX3S", 1)], "TECH10", "Casa"),
    ("camilo_dev", [("SONY-WH1000", 1)], None, "Universidad"),
    ("caro_dev", [("APPL-IP16", 1), ("ANKR-PB20", 2)], "BIENVENIDA15", "Apartamento"),
    ("ana_compras", [("XIAO-RN13", 1), ("JBL-FLIP6", 1)], None, "Casa"),
]

# Carrito que queda abierto para mostrar en la demo (sin pagar)
CARRITO_ABIERTO: tuple[str, list[tuple[str, int]]] = (
    "camilo_dev", [("LG-UW34", 1), ("KEYC-K2", 1)],
)

# Visitas a fichas de producto para que el ranking (#20) tenga datos
VISITAS: dict[str, int] = {
    "APPL-MBA-M3": 12, "SONY-WH1000": 9, "APPL-IP16": 7, "LG-UW34": 5,
    "SAMS-S24": 4, "LOGI-MX3S": 3, "ASUS-ROG-G14": 2,
}


# ── Ejecución ─────────────────────────────────────────────────────────

class Seed:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.errores: list[str] = []

    def exigir(self, descripcion: str, status: int, cuerpo: Any, *esperados: int) -> bool:
        if status in esperados:
            return True
        self.errores.append(f"{descripcion}: {status} {cuerpo}")
        print(f"  ERROR {descripcion}: {status} {cuerpo}")
        return False

    def productos(self) -> None:
        sin_indexar = 0
        for pid, nombre, categoria, marca, precio, stock, descripcion in PRODUCTOS:
            st, b = self.api.pedir("POST", "/products", {
                "id": pid, "nombre": nombre, "categoria": categoria, "marca": marca,
                "precio": precio, "stock": stock, "descripcion": descripcion,
            })
            if self.exigir(f"producto {pid}", st, b, 201) and not b.get("_indexado_busqueda"):
                sin_indexar += 1
        nota = f" ({sin_indexar} sin indexar: ¿Elasticsearch arriba?)" if sin_indexar else ""
        print(f"Productos: {len(PRODUCTOS)}{nota}")

    def usuarios(self) -> None:
        for u in USUARIOS:
            st, b = self.api.pedir("POST", "/users", {
                "username": u["username"], "nombre": u["nombre"], "email": u["email"],
            })
            self.exigir(f"usuario {u['username']}", st, b, 201, 409)   # 409 = ya existía
            for nombre, datos in u["direcciones"]:
                st, b = self.api.pedir("PUT", f"/users/{u['username']}/addresses/{nombre}", datos)
                self.exigir(f"dirección {u['username']}/{nombre}", st, b, 200)
            for pid in u["favoritos"]:
                st, b = self.api.pedir("PUT", f"/users/{u['username']}/wishlist/{pid}")
                self.exigir(f"favorito {u['username']}/{pid}", st, b, 200)
        print(f"Usuarios: {len(USUARIOS)} (con direcciones y favoritos)")

    def resenas(self) -> None:
        creadas = 0
        for pid in sorted({r[0] for r in RESENAS}):
            # Las reseñas no son idempotentes (la SK lleva la fecha): si el
            # producto ya tiene, se asume que el seed ya corrió y se salta.
            st, b = self.api.pedir("GET", f"/products/{pid}/reviews")
            if st == 200 and b["total"] > 0:
                continue
            for _, username, calificacion, comentario in (r for r in RESENAS if r[0] == pid):
                st, b = self.api.pedir("POST", f"/products/{pid}/reviews", {
                    "username": username, "calificacion": calificacion, "comentario": comentario,
                })
                creadas += self.exigir(f"reseña {pid}/{username}", st, b, 201)
        print(f"Reseñas nuevas: {creadas}")

    def cupones(self) -> None:
        for c in CUPONES:
            st, b = self.api.pedir("POST", "/coupons", c)
            self.exigir(f"cupón {c['codigo']}", st, b, 201)
        print(f"Cupones: {len(CUPONES)} (válido, con mínimo, futuro, vencido, desactivado)")

    def promocion(self) -> None:
        st, b = self.api.pedir("POST", "/promotions", PROMOCION)
        if self.exigir("promoción del día", st, b, 201):
            en_cache = "en Redis" if b.get("_en_cache") else "SIN Redis"
            print(f"Promoción {b['fecha']}: {', '.join(PROMOCION['productos'])} "
                  f"-{PROMOCION['descuento_pct']}% ({en_cache})")

    def llenar_carrito(self, username: str, lineas: list[tuple[str, int]]) -> None:
        for pid, cantidad in lineas:
            st, b = self.api.pedir("PUT", f"/cart/{pid}", {"cantidad": cantidad}, usuario=username)
            self.exigir(f"carrito {username}/{pid}", st, b, 200)

    def ordenes(self) -> None:
        con_ordenes: set[str] = set()
        for username in {o[0] for o in ORDENES}:
            st, b = self.api.pedir("GET", f"/users/{username}/orders")
            if st == 200 and b["total"] > 0:
                con_ordenes.add(username)

        creadas = 0
        for username, lineas, cupon, direccion in ORDENES:
            if username in con_ordenes:
                continue
            self.llenar_carrito(username, lineas)
            body: dict[str, Any] = {}
            if cupon:
                body["cupon"] = cupon
            if direccion:
                body["direccion"] = direccion
            st, b = self.api.pedir("POST", "/orders", body, usuario=username)
            if self.exigir(f"orden de {username}", st, b, 201):
                creadas += 1
                print(f"  {b['orderId']} {username}: total {b['total']:,} COP")
        print(f"Órdenes nuevas: {creadas}")

        username, lineas = CARRITO_ABIERTO
        self.llenar_carrito(username, lineas)
        print(f"Carrito abierto de {username}: {', '.join(p for p, _ in lineas)}")

    def visitas(self) -> None:
        # El ranking suma en cada GET: si los productos del seed ya tienen
        # vistas, el seed ya corrió y no se vuelven a sumar.
        st, b = self.api.pedir("GET", "/products/ranking?limite=50")
        ya_vistos = {r["productId"] for r in b.get("ranking", [])} if st == 200 else set()
        if ya_vistos & VISITAS.keys():
            print("Ranking: ya tenía visitas del seed, no se suman más")
            return
        for pid, veces in VISITAS.items():
            for _ in range(veces):
                self.api.pedir("GET", f"/products/{pid}")
        st, b = self.api.pedir("GET", "/products/ranking?limite=5")
        top = ", ".join(f"{r['productId']}({r['vistas']})" for r in b.get("ranking", []))
        print(f"Ranking de más vistos: {top or '(vacío: ¿Redis arriba?)'}")


def main() -> int:
    api = Api(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"API: {api.base}")
    print(f"Fecha de referencia (Bogotá): {HOY}")
    print()

    st, salud = api.pedir("GET", "/health")
    if st != 200:
        print(f"Aviso: /health respondió {st}: {salud}")

    seed = Seed(api)
    seed.productos()
    seed.usuarios()
    seed.resenas()
    seed.cupones()
    seed.promocion()     # antes de las órdenes: así el checkout aplica la promoción
    seed.ordenes()
    seed.visitas()

    print()
    if seed.errores:
        print(f"Terminó con {len(seed.errores)} errores")
        return 1
    print("Seed completo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
