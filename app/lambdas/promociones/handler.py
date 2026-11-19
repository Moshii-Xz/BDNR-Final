"""
Lambda promociones
──────────────────
Promoción del día: 2 productos con descuento durante 1 día (patrones #16 y #17).

Rutas soportadas (se despacha por event["resource"]):
  GET  /promotions/today  → #16 Promoción vigente hoy (Redis → DynamoDB)
  POST /promotions        → #17 Crear/reemplazar la promoción de un día
                            body: {"productos": [id1, id2], "descuento_pct": 50,
                                   "fecha": "YYYY-MM-DD" (opcional, por defecto hoy)}

Modelo (llaves en claves.py):
  Promotion: PK = SK = PROMO#<YYYY-MM-DD de Bogotá>  → la fecha es la llave natural

Patrón de caché: Write-Through con TTL hasta la medianoche de Bogotá.
Se escribe una vez al día y se lee en cada visita al inicio; al crearla se
guarda en DynamoDB y de inmediato en Redis, así el primer visitante del día
ya encuentra la promoción en caché (con Cache-Aside la pagaría él). El TTL
son los segundos que faltan para medianoche: vence justo cuando deja de ser
válida, sin job de limpieza. Si Redis se reinició, el GET la recarga desde
DynamoDB con el mismo TTL (respaldo).
"""
import json
import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeAlias

from botocore.exceptions import ClientError

import claves
from cache import ZONA_BOGOTA, cache_get, cache_key_promocion, cache_set_hasta_medianoche
from db import TABLE_NAME, dynamodb, tabla
from respuestas import RespuestaApi, cuerpo_json, desde_dynamo, respuesta, respuesta_error

Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

PRODUCTOS_POR_PROMOCION: int = 2
CENTAVOS = Decimal("0.01")


def _hoy() -> date:
    return datetime.now(ZONA_BOGOTA).date()


def _publico(item: Item) -> Item:
    return desde_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "tipo")})


def _leer_productos(product_ids: list[str]) -> dict[str, Item]:
    pendientes: dict[str, Any] = {
        TABLE_NAME: {
            "Keys": [
                {"PK": claves.pk_producto(p), "SK": claves.sk_producto(p)} for p in product_ids
            ]
        }
    }
    encontrados: dict[str, Item] = {}
    while pendientes:
        r = dynamodb.batch_get_item(RequestItems=pendientes)
        for item in r["Responses"].get(TABLE_NAME, []):
            encontrados[item["id"]] = item
        pendientes = r.get("UnprocessedKeys") or {}
    return encontrados


# ── #16 Promoción de hoy ──────────────────────────────────────────────

def promocion_de_hoy() -> RespuestaApi:
    fecha: str = _hoy().isoformat()
    clave: str = cache_key_promocion(fecha)

    cached = cache_get(clave)
    if cached:
        return respuesta(200, {**cached, "_fuente": "cache"})

    try:
        item = tabla().get_item(
            Key={"PK": claves.pk_promocion(fecha), "SK": claves.sk_promocion(fecha)}
        ).get("Item")
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al consultar la base de datos")

    if not item:
        return respuesta_error(404, f"No hay promoción para hoy ({fecha})")

    # Respaldo si Redis perdió la clave (reinicio): se vuelve a cargar
    promocion = _publico(item)
    cache_set_hasta_medianoche(clave, promocion)
    return respuesta(200, {**promocion, "_fuente": "dynamodb"})


# ── #17 Crear/reemplazar promoción ────────────────────────────────────

def crear_promocion(body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    product_ids = datos.get("productos")
    if (
        not isinstance(product_ids, list)
        or len(product_ids) != PRODUCTOS_POR_PROMOCION
        or not all(isinstance(p, str) and p for p in product_ids)
        or len(set(product_ids)) != PRODUCTOS_POR_PROMOCION
    ):
        return respuesta_error(
            400, f"productos debe ser una lista de {PRODUCTOS_POR_PROMOCION} ids distintos"
        )

    descuento = datos.get("descuento_pct")
    if isinstance(descuento, bool) or not isinstance(descuento, int) or not 1 <= descuento <= 100:
        return respuesta_error(400, "descuento_pct debe ser un entero entre 1 y 100")

    hoy = _hoy()
    try:
        fecha = date.fromisoformat(datos["fecha"]) if datos.get("fecha") else hoy
    except (TypeError, ValueError):
        return respuesta_error(400, "fecha debe tener formato YYYY-MM-DD")
    if fecha < hoy:
        return respuesta_error(400, "No se puede crear una promoción para un día pasado")

    productos = _leer_productos(product_ids)
    faltantes = [p for p in product_ids if p not in productos]
    if faltantes:
        return respuesta_error(404, f"Productos no encontrados: {', '.join(faltantes)}")

    # Copia de nombre y precios: el banner se arma sin leer el catálogo
    lineas: list[Item] = []
    for pid in product_ids:
        precio = Decimal(str(productos[pid]["precio"]))
        lineas.append({
            "productId": pid,
            "nombre": productos[pid].get("nombre", ""),
            "precio": precio,
            "precio_promocion": (precio * (100 - descuento) / 100).quantize(
                CENTAVOS, ROUND_HALF_UP
            ),
        })

    fecha_iso = fecha.isoformat()
    item: Item = {
        "PK": claves.pk_promocion(fecha_iso),
        "SK": claves.sk_promocion(fecha_iso),
        "tipo": "PROMOTION",
        "fecha": fecha_iso,
        "descuento_pct": descuento,
        "productos": lineas,
        "creada_en": datetime.now(ZONA_BOGOTA).isoformat(),
    }

    # 1. DynamoDB (fuente de verdad). Un PutItem: si ya había promoción ese
    #    día, la reemplaza.
    tabla().put_item(Item=item)
    logger.info("DynamoDB PUT promoción fecha=%s", fecha_iso)

    promocion = _publico(item)

    # 2. Write-Through: solo si es la de hoy. Una promoción futura no se
    #    cachea aún (el TTL "hasta medianoche" la vencería antes de su día);
    #    la cargará el GET del día que corresponda.
    en_cache = False
    if fecha == hoy:
        en_cache = cache_set_hasta_medianoche(cache_key_promocion(fecha_iso), promocion)

    return respuesta(201, {**promocion, "_en_cache": en_cache})


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")

    logger.info("REQUEST %s %s", metodo, recurso)

    if (metodo, recurso) == ("GET", "/promotions/today"):
        return promocion_de_hoy()
    if (metodo, recurso) == ("POST", "/promotions"):
        return crear_promocion(event.get("body"))
    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
