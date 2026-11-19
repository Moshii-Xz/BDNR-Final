"""
Lambda usuarios
───────────────
Datos de la cuenta del usuario (patrones de acceso #9, #10 y #11).

Rutas soportadas (se despacha por event["resource"]):
  GET /users/{username}            → #9  Perfil (GetItem)
  GET /users/{username}/addresses  → #10 Direcciones (Query begins_with ADDRESS#)
  GET /users/{username}/wishlist   → #11 Lista de deseos (Query begins_with WISH#)

Modelo (llaves en claves.py) — todo bajo la partición del usuario:
  User:         PK = USER#<username>   SK = USER#<username>
  Address:      PK = USER#<username>   SK = ADDRESS#<nombreDirección>
  WishlistItem: PK = USER#<username>   SK = WISH#<productId>

Sin caché en ninguna de las tres: el perfil y las direcciones son de
frecuencia media/baja, y la wishlist cambia con cada "me gusta".
"""
import logging
from typing import Any, TypeAlias

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import claves
from db import tabla
from respuestas import RespuestaApi, desde_dynamo, respuesta, respuesta_error

Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ── Helpers ───────────────────────────────────────────────────────────

def _publico(item: Item) -> Item:
    return desde_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "tipo")})


def _query_particion_usuario(username: str, prefijo_sk: str) -> list[Item]:
    """
    Query a USER#<username> filtrando por prefijo de SK: la misma partición
    guarda perfil, carrito, direcciones, wishlist y órdenes.
    """
    items: list[Item] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(claves.pk_usuario(username))
        & Key("SK").begins_with(prefijo_sk),
    }
    while True:
        pagina: dict[str, Any] = tabla().query(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            return items
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]


# ── #9 Perfil ─────────────────────────────────────────────────────────

def obtener_perfil(username: str) -> RespuestaApi:
    try:
        resultado: dict[str, Any] = tabla().get_item(
            Key={"PK": claves.pk_usuario(username), "SK": claves.sk_usuario(username)}
        )
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al consultar la base de datos")

    item = resultado.get("Item")
    if not item:
        return respuesta_error(404, f"Usuario '{username}' no encontrado")
    return respuesta(200, _publico(item))


# ── #10 Direcciones y #11 Wishlist ────────────────────────────────────

def listar_direcciones(username: str) -> RespuestaApi:
    direcciones = [_publico(i) for i in _query_particion_usuario(username, "ADDRESS#")]
    return respuesta(200, {
        "username": username,
        "direcciones": direcciones,
        "total": len(direcciones),
    })


def listar_wishlist(username: str) -> RespuestaApi:
    productos = [_publico(i) for i in _query_particion_usuario(username, "WISH#")]
    return respuesta(200, {
        "username": username,
        "productos": productos,
        "total": len(productos),
    })


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    if metodo != "GET":
        return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")

    username: str = params.get("username", "")
    rutas = {
        "/users/{username}": obtener_perfil,
        "/users/{username}/addresses": listar_direcciones,
        "/users/{username}/wishlist": listar_wishlist,
    }
    accion = rutas.get(recurso)
    if accion is None:
        return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
    return accion(username)
