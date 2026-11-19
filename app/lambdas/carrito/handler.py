"""
Lambda carrito
──────────────
Carrito de compras del usuario (patrón de acceso #7).

Rutas soportadas (se despacha por event["resource"]):
  GET    /cart               → Ver el carrito completo (un solo Query)
  PUT    /cart/{productId}   → Agregar o cambiar la cantidad (PutItem = upsert)
  DELETE /cart/{productId}   → Quitar un producto (DeleteItem por llave exacta)

El usuario se identifica con el header X-Username (o ?username= como
respaldo): el proyecto no tiene autenticación y las rutas del diseño no
llevan el usuario en el path.

Modelo (llaves en claves.py):
  CartItem: PK = USER#<username>   SK = CART#<productId>

Sin caché a propósito: el carrito cambia en cada clic y mostrarlo
desactualizado sería un error visible; el Query a una sola partición ya
es barato. Tampoco se lee el producto antes de escribir: el nombre/precio
se muestran desde el detalle (#1, cacheado) y el checkout valida stock.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, TypeAlias

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import claves
from db import tabla
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

CANTIDAD_MAXIMA: int = 99
PREFIJO_CARRITO: str = "CART#"


# ── Helpers ───────────────────────────────────────────────────────────

def _publico(item: Item) -> Item:
    return desde_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "tipo")})


def _llave(username: str, product_id: str) -> dict[str, str]:
    return {"PK": claves.pk_usuario(username), "SK": claves.sk_cart_item(product_id)}


# ── GET /cart ─────────────────────────────────────────────────────────

def ver_carrito(username: str) -> RespuestaApi:
    items: list[Item] = []
    kwargs: dict[str, Any] = {
        # La partición USER#<u> también guarda perfil, direcciones, wishlist
        # y órdenes: begins_with("CART#") trae solo las líneas del carrito.
        "KeyConditionExpression": Key("PK").eq(claves.pk_usuario(username))
        & Key("SK").begins_with(PREFIJO_CARRITO),
    }
    while True:
        pagina: dict[str, Any] = tabla().query(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            break
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]

    lineas = [_publico(i) for i in items]
    return respuesta(200, {
        "username": username,
        "items": lineas,
        "total_items": len(lineas),
        "total_unidades": sum(linea["cantidad"] for linea in lineas),
    })


# ── PUT /cart/{productId} ─────────────────────────────────────────────

def poner_en_carrito(username: str, product_id: str, body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    cantidad = datos.get("cantidad", 1)
    if (
        isinstance(cantidad, bool)
        or not isinstance(cantidad, int)
        or not 1 <= cantidad <= CANTIDAD_MAXIMA
    ):
        return respuesta_error(
            400,
            f"cantidad debe ser un entero entre 1 y {CANTIDAD_MAXIMA} "
            "(para quitar el producto use DELETE)",
        )

    item: Item = {
        **_llave(username, product_id),
        "tipo": "CART_ITEM",
        "productId": product_id,
        "cantidad": cantidad,
        "actualizado_en": datetime.now(timezone.utc).isoformat(),
    }
    # PUT es idempotente: fija la cantidad (no la suma), así repetir la
    # petición no duplica unidades.
    tabla().put_item(Item=item)
    logger.info("DynamoDB PUT carrito usuario=%s producto=%s cantidad=%s",
                username, product_id, cantidad)

    return respuesta(200, _publico(item))


# ── DELETE /cart/{productId} ──────────────────────────────────────────

def quitar_de_carrito(username: str, product_id: str) -> RespuestaApi:
    try:
        tabla().delete_item(
            Key=_llave(username, product_id),
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respuesta_error(404, f"El producto '{product_id}' no está en el carrito")
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al quitar el producto del carrito")

    logger.info("DynamoDB DELETE carrito usuario=%s producto=%s", username, product_id)
    return respuesta(200, {"mensaje": f"Producto '{product_id}' quitado del carrito"})


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    username: str | None = username_de_evento(event)
    if not username:
        return respuesta_error(400, "Falta el usuario: envíe el header X-Username")

    if recurso == "/cart" and metodo == "GET":
        return ver_carrito(username)
    if recurso == "/cart/{productId}" and metodo == "PUT":
        return poner_en_carrito(username, params["productId"], event.get("body"))
    if recurso == "/cart/{productId}" and metodo == "DELETE":
        return quitar_de_carrito(username, params["productId"])

    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
