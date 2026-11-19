"""
Lambda ordenes
──────────────
Checkout y consulta de órdenes (patrones de acceso #12 a #15).

Rutas soportadas (se despacha por event["resource"]):
  POST /orders                           → #12 Checkout transaccional desde el carrito
  GET  /users/{username}/orders          → #14 Historial, recientes primero
  GET  /users/{username}/orders/ultima   → #13 Última orden (con sus líneas)
  GET  /orders/{orderId}                 → #15 Orden por número (GSI1, Cache-Aside)

Modelo (llaves en claves.py):
  Order:     PK = USER#<u>   SK = ORDER#<fechaISO>#<orderId>
             GSI1PK = ORDER#<orderId>   GSI1SK = ORDER#HEADER
  OrderItem: PK = USER#<u>   SK = ORDER#<fechaISO>#<orderId>#ITEM#<productId>
             GSI1PK = ORDER#<orderId>   GSI1SK = ITEM#<productId>

Las líneas copian nombre y precio del producto al momento de pagar: la
orden es un registro histórico y no debe cambiar si después cambia el
catálogo. Por eso también es segura de cachear (#15).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeAlias

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

import claves
from cache import (
    ZONA_BOGOTA,
    cache_delete,
    cache_get,
    cache_key_orden,
    cache_key_producto,
    cache_key_promocion,
    cache_set,
)
from cupones import evaluar_cupon, normalizar_codigo
from db import TABLE_NAME, dynamodb, tabla
from respuestas import (
    RespuestaApi,
    cuerpo_json,
    desde_dynamo,
    respuesta,
    respuesta_error,
    username_de_evento,
)

Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

# TransactWriteItems admite 100 operaciones: 1 header + 3 por línea
# (línea de la orden, borrar del carrito, descontar stock) → 33 líneas.
MAX_LINEAS_ORDEN: int = 33
# Una orden pagada no cambia: TTL más largo que el del catálogo (5 min)
TTL_CACHE_ORDEN: int = 600
CENTAVOS = Decimal("0.01")

_ATRIBUTOS_LLAVE = frozenset({"PK", "SK", "GSI1PK", "GSI1SK", "tipo"})


class ErrorCheckout(Exception):
    def __init__(self, status: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


# ── Helpers ───────────────────────────────────────────────────────────

def _publico(item: Item) -> Item:
    return desde_dynamo({k: v for k, v in item.items() if k not in _ATRIBUTOS_LLAVE})


def _query_todo(**kwargs: Any) -> list[Item]:
    items: list[Item] = []
    while True:
        pagina: dict[str, Any] = tabla().query(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            return items
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]


def _armar_orden(items: list[Item]) -> Item | None:
    """Junta header + líneas (en cualquier orden) en un solo objeto."""
    header = next((i for i in items if i.get("tipo") == "ORDER"), None)
    if header is None:
        return None
    lineas = sorted(
        (_publico(i) for i in items if i.get("tipo") == "ORDER_ITEM"),
        key=lambda linea: linea["productId"],
    )
    return {**_publico(header), "items": lineas}


def _leer_productos(product_ids: list[str]) -> dict[str, Item]:
    """BatchGetItem de los productos del carrito (máx. 33 < 100 llaves)."""
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


def _promocion_vigente() -> tuple[str | None, Decimal, set[str]]:
    """
    Promoción del día (fecha de Bogotá): (fecha, descuento_pct, productIds).
    Primero Redis —ya está ahí por el Write-Through de promociones— y si no,
    DynamoDB. Sin promoción retorna (None, 0, vacío).
    """
    fecha = datetime.now(ZONA_BOGOTA).date().isoformat()
    promo: Item | None = cache_get(cache_key_promocion(fecha))
    if promo is None:
        promo = tabla().get_item(
            Key={"PK": claves.pk_promocion(fecha), "SK": claves.sk_promocion(fecha)}
        ).get("Item")
    if not promo:
        return None, Decimal("0"), set()
    ids = {p["productId"] for p in promo.get("productos", [])}
    return fecha, Decimal(str(promo["descuento_pct"])), ids


# ── #12 Checkout ──────────────────────────────────────────────────────

def _preparar_orden(username: str, datos: dict[str, Any]) -> tuple[Item, list[Item], list[Item]]:
    """
    Lee carrito, productos, cupón y dirección, y calcula los totales.
    Retorna (header, lineas, lineas_carrito). Lanza ErrorCheckout.
    """
    carrito: list[Item] = _query_todo(
        KeyConditionExpression=Key("PK").eq(claves.pk_usuario(username))
        & Key("SK").begins_with("CART#"),
    )
    if not carrito:
        raise ErrorCheckout(400, "El carrito está vacío")
    if len(carrito) > MAX_LINEAS_ORDEN:
        raise ErrorCheckout(400, f"Una orden admite máximo {MAX_LINEAS_ORDEN} productos distintos")

    productos = _leer_productos([c["productId"] for c in carrito])
    faltantes = [c["productId"] for c in carrito if c["productId"] not in productos]
    if faltantes:
        raise ErrorCheckout(409, f"Productos que ya no existen: {', '.join(faltantes)}")

    sin_stock = [
        c["productId"] for c in carrito
        if productos[c["productId"]].get("stock", 0) < c["cantidad"]
    ]
    if sin_stock:
        raise ErrorCheckout(409, f"Stock insuficiente para: {', '.join(sin_stock)}")

    order_id: str = f"ORD-{uuid.uuid4().hex[:10].upper()}"
    fecha: str = datetime.now(timezone.utc).isoformat()

    # Los productos de la promoción del día se cobran con su descuento. Se
    # calcula sobre el precio actual (no el copiado en la promoción) para
    # que un cambio de precio posterior no deje un precio promocional viejo.
    fecha_promo, pct_promo, ids_promo = _promocion_vigente()

    lineas: list[Item] = []
    subtotal = Decimal("0")
    for c in carrito:
        producto = productos[c["productId"]]
        precio_lista = Decimal(str(producto["precio"]))
        en_promo = c["productId"] in ids_promo
        precio = (
            (precio_lista * (100 - pct_promo) / 100).quantize(CENTAVOS, ROUND_HALF_UP)
            if en_promo else precio_lista
        )
        cantidad = int(c["cantidad"])
        total_linea = (precio * cantidad).quantize(CENTAVOS, ROUND_HALF_UP)
        subtotal += total_linea
        extra_promo: Item = (
            {"precio_lista": precio_lista, "promocion": fecha_promo, "descuento_promo_pct": pct_promo}
            if en_promo else {}
        )
        lineas.append({
            "PK": claves.pk_usuario(username),
            "SK": claves.sk_orden_item(fecha, order_id, c["productId"]),
            "GSI1PK": claves.gsi1_pk_orden(order_id),
            "GSI1SK": claves.gsi1_sk_orden_item(c["productId"]),
            "tipo": "ORDER_ITEM",
            "orderId": order_id,
            "productId": c["productId"],
            "nombre": producto.get("nombre", ""),
            "precio_unitario": precio,
            "cantidad": cantidad,
            "subtotal": total_linea,
            **extra_promo,
        })

    # Cupón opcional: se vuelve a validar aquí, con el subtotal real
    descuento = Decimal("0")
    codigo_cupon: str | None = None
    if datos.get("cupon"):
        codigo_cupon = normalizar_codigo(str(datos["cupon"]))
        cupon = tabla().get_item(
            Key={"PK": claves.pk_cupon(codigo_cupon), "SK": claves.sk_cupon(codigo_cupon)}
        ).get("Item")
        if not cupon:
            raise ErrorCheckout(400, f"El cupón '{codigo_cupon}' no existe")
        valido, motivo = evaluar_cupon(cupon, subtotal=subtotal)
        if not valido:
            raise ErrorCheckout(400, f"Cupón '{codigo_cupon}' no aplicable: {motivo}")
        descuento = (subtotal * Decimal(str(cupon["descuento_pct"])) / 100).quantize(
            CENTAVOS, ROUND_HALF_UP
        )

    # Dirección opcional: se copia a la orden (si el usuario la edita
    # después, la orden conserva a dónde se envió realmente)
    direccion: Item | None = None
    if datos.get("direccion"):
        nombre_dir = str(datos["direccion"])
        direccion = tabla().get_item(
            Key={"PK": claves.pk_usuario(username), "SK": claves.sk_direccion(nombre_dir)}
        ).get("Item")
        if not direccion:
            raise ErrorCheckout(400, f"La dirección '{nombre_dir}' no existe")
        direccion = {k: v for k, v in direccion.items() if k not in _ATRIBUTOS_LLAVE}

    header: Item = {
        "PK": claves.pk_usuario(username),
        "SK": claves.sk_orden(fecha, order_id),
        "GSI1PK": claves.gsi1_pk_orden(order_id),
        "GSI1SK": claves.gsi1_sk_orden_header(),
        "tipo": "ORDER",
        "orderId": order_id,
        "username": username,
        "fecha": fecha,
        "estado": "PAGADA",
        "subtotal": subtotal,
        "descuento": descuento,
        "total": subtotal - descuento,
        "total_unidades": sum(int(c["cantidad"]) for c in carrito),
    }
    if codigo_cupon:
        header["cupon"] = codigo_cupon
    if direccion:
        header["direccion"] = direccion

    return header, lineas, carrito


def _operaciones_transaccion(header: Item, lineas: list[Item], carrito: list[Item]) -> list[Item]:
    ops: list[Item] = [{
        "Put": {
            "TableName": TABLE_NAME,
            "Item": header,
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }]
    for linea in lineas:
        ops.append({"Put": {"TableName": TABLE_NAME, "Item": linea}})
    for c in carrito:
        # Si el usuario cambió la cantidad mientras pagaba, se cancela todo
        ops.append({
            "Delete": {
                "TableName": TABLE_NAME,
                "Key": {"PK": c["PK"], "SK": c["SK"]},
                "ConditionExpression": "cantidad = :cant",
                "ExpressionAttributeValues": {":cant": c["cantidad"]},
            }
        })
        # Descuento de stock condicionado: evita vender más de lo que hay
        # aunque dos usuarios paguen el último ítem al mismo tiempo
        ops.append({
            "Update": {
                "TableName": TABLE_NAME,
                "Key": {
                    "PK": claves.pk_producto(c["productId"]),
                    "SK": claves.sk_producto(c["productId"]),
                },
                "UpdateExpression": "SET stock = stock - :cant",
                "ConditionExpression": "attribute_exists(PK) AND stock >= :cant",
                "ExpressionAttributeValues": {":cant": c["cantidad"]},
            }
        })
    return ops


def crear_orden(username: str, body_raw: str | None) -> RespuestaApi:
    """
    Única escritura que toca varias entidades: crea header + líneas, vacía
    el carrito y descuenta stock en un solo TransactWriteItems (todo o nada).
    """
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    try:
        header, lineas, carrito = _preparar_orden(username, datos)
    except ErrorCheckout as e:
        return respuesta_error(e.status, e.mensaje)

    try:
        # meta.client del resource acepta tipos nativos de Python (serializa solo)
        tabla().meta.client.transact_write_items(
            TransactItems=_operaciones_transaccion(header, lineas, carrito)
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            logger.warning("Checkout cancelado usuario=%s: %s", username, e)
            return respuesta_error(
                409,
                "El carrito o el stock cambiaron durante el pago; revise el carrito e intente de nuevo",
            )
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al crear la orden")

    logger.info("Orden %s creada usuario=%s lineas=%d", header["orderId"], username, len(lineas))

    # El stock cambió: el detalle cacheado de esos productos quedó viejo
    for c in carrito:
        cache_delete(cache_key_producto(c["productId"]))

    orden = {**_publico(header), "items": [_publico(linea) for linea in lineas]}
    return respuesta(201, orden)


# ── #14 Historial y #13 Última orden ──────────────────────────────────

def historial_ordenes(username: str) -> RespuestaApi:
    # begins_with("ORDER#") trae headers y líneas; el filtro deja solo los
    # headers (las líneas se ven en el detalle de cada orden).
    headers = _query_todo(
        KeyConditionExpression=Key("PK").eq(claves.pk_usuario(username))
        & Key("SK").begins_with("ORDER#"),
        FilterExpression=Attr("tipo").eq("ORDER"),
        ScanIndexForward=False,
    )
    ordenes = [_publico(h) for h in headers]
    return respuesta(200, {"username": username, "ordenes": ordenes, "total": len(ordenes)})


def ultima_orden(username: str) -> RespuestaApi:
    """
    Query en reversa con Limit=1. Ojo: la SK de una línea
    (ORDER#<fecha>#<id>#ITEM#<p>) ordena DESPUÉS de la de su header
    (ORDER#<fecha>#<id>), así que el primer resultado puede ser una línea.
    De cualquiera de los dos se saca el prefijo ORDER#<fecha>#<id> y se trae
    la orden completa con un segundo Query de la misma partición.
    """
    pk = claves.pk_usuario(username)
    primero = tabla().query(
        KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with("ORDER#"),
        ScanIndexForward=False,
        Limit=1,
        ProjectionExpression="SK",
    ).get("Items", [])
    if not primero:
        return respuesta_error(404, f"El usuario '{username}' no tiene órdenes")

    prefijo_orden: str = primero[0]["SK"].split("#ITEM#")[0]
    items = _query_todo(KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(prefijo_orden))
    orden = _armar_orden(items)
    if orden is None:
        return respuesta_error(404, f"El usuario '{username}' no tiene órdenes")
    return respuesta(200, orden)


# ── #15 Orden por número (GSI1 + Cache-Aside) ─────────────────────────

def obtener_orden(order_id: str) -> RespuestaApi:
    clave = cache_key_orden(order_id)
    cached = cache_get(clave)
    if cached:
        return respuesta(200, {**cached, "_fuente": "cache"})

    items = _query_todo(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(claves.gsi1_pk_orden(order_id)),
    )
    orden = _armar_orden(items)
    if orden is None:
        return respuesta_error(404, f"Orden '{order_id}' no encontrada")

    cache_set(clave, orden, ttl=TTL_CACHE_ORDEN)
    return respuesta(200, {**orden, "_fuente": "dynamodb"})


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    if (metodo, recurso) == ("POST", "/orders"):
        username = username_de_evento(event)
        if not username:
            return respuesta_error(400, "Falta el usuario: envíe el header X-Username")
        return crear_orden(username, event.get("body"))

    if metodo == "GET":
        if recurso == "/orders/{orderId}":
            return obtener_orden(params["orderId"])
        if recurso == "/users/{username}/orders":
            return historial_ordenes(params["username"])
        if recurso == "/users/{username}/orders/ultima":
            return ultima_orden(params["username"])

    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
