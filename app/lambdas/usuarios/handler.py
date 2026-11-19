"""
Lambda usuarios
───────────────
Datos de la cuenta del usuario (patrones de acceso #9, #10 y #11) y sus
escrituras.

Rutas soportadas (se despacha por event["resource"]):
  POST   /users                                 → Registrar usuario
  GET    /users/{username}                      → #9  Perfil (GetItem)
  GET    /users/{username}/addresses            → #10 Direcciones (Query begins_with ADDRESS#)
  PUT    /users/{username}/addresses/{nombre}   → Guardar/actualizar una dirección
  DELETE /users/{username}/addresses/{nombre}   → Borrar una dirección
  GET    /users/{username}/wishlist             → #11 Lista de deseos (Query begins_with WISH#)
  PUT    /users/{username}/wishlist/{productId} → Marcar favorito (botón de corazón)
  DELETE /users/{username}/wishlist/{productId} → Quitar favorito

Las escrituras usan las mismas llaves que las lecturas: cada una es un
PutItem/DeleteItem por llave exacta, sin índices nuevos.

Modelo (llaves en claves.py) — todo bajo la partición del usuario:
  User:         PK = USER#<username>   SK = USER#<username>
  Address:      PK = USER#<username>   SK = ADDRESS#<nombreDirección>
  WishlistItem: PK = USER#<username>   SK = WISH#<productId>

Sin caché: el perfil y las direcciones son de frecuencia media/baja, y la
wishlist cambia con cada "me gusta".
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, TypeAlias

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import claves
from db import tabla
from respuestas import RespuestaApi, cuerpo_json, desde_dynamo, respuesta, respuesta_error

Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

# El username forma parte de la llave (USER#<username>): sin '#' ni espacios
PATRON_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
CAMPOS_DIRECCION: tuple[str, ...] = ("direccion", "ciudad")
NIVEL_INICIAL: str = "bronce"


# ── Helpers ───────────────────────────────────────────────────────────

def _publico(item: Item) -> Item:
    return desde_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "tipo")})


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _llave_perfil(username: str) -> dict[str, str]:
    return {"PK": claves.pk_usuario(username), "SK": claves.sk_usuario(username)}


def _existe(llave: dict[str, str]) -> bool:
    return "Item" in tabla().get_item(Key=llave, ProjectionExpression="PK")


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


def _borrar(llave: dict[str, str], descripcion: str) -> RespuestaApi:
    try:
        tabla().delete_item(Key=llave, ConditionExpression="attribute_exists(PK)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respuesta_error(404, f"{descripcion} no existe")
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al eliminar")
    return respuesta(200, {"mensaje": f"{descripcion} eliminada"})


# ── Registro y #9 Perfil ──────────────────────────────────────────────

def registrar_usuario(body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    username = str(datos.get("username") or "")
    if not PATRON_USERNAME.match(username):
        return respuesta_error(
            400, "username debe tener 3-30 caracteres: letras, números, '_', '.' o '-'"
        )
    if not datos.get("nombre"):
        return respuesta_error(400, "Falta el campo obligatorio: nombre")

    item: Item = {
        **_llave_perfil(username),
        "tipo": "USER",
        "username": username,
        "nombre": datos["nombre"],
        "email": datos.get("email", ""),
        "nivel": NIVEL_INICIAL,
        "registrado_en": _ahora(),
    }
    try:
        # El username es la llave: no se puede pisar una cuenta existente
        tabla().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respuesta_error(409, f"El usuario '{username}' ya existe")
        raise
    logger.info("DynamoDB PUT usuario=%s", username)
    return respuesta(201, _publico(item))


def obtener_perfil(username: str) -> RespuestaApi:
    try:
        resultado: dict[str, Any] = tabla().get_item(Key=_llave_perfil(username))
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al consultar la base de datos")

    item = resultado.get("Item")
    if not item:
        return respuesta_error(404, f"Usuario '{username}' no encontrado")
    return respuesta(200, _publico(item))


# ── #10 Direcciones ───────────────────────────────────────────────────

def listar_direcciones(username: str) -> RespuestaApi:
    direcciones = [_publico(i) for i in _query_particion_usuario(username, "ADDRESS#")]
    return respuesta(200, {
        "username": username,
        "direcciones": direcciones,
        "total": len(direcciones),
    })


def guardar_direccion(username: str, nombre: str, body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    faltantes = [c for c in CAMPOS_DIRECCION if not datos.get(c)]
    if faltantes:
        return respuesta_error(400, f"Faltan campos obligatorios: {', '.join(faltantes)}")
    if not _existe(_llave_perfil(username)):
        return respuesta_error(404, f"Usuario '{username}' no encontrado")

    item: Item = {
        "PK": claves.pk_usuario(username),
        "SK": claves.sk_direccion(nombre),
        "tipo": "ADDRESS",
        "nombre": nombre,
        "direccion": datos["direccion"],
        "ciudad": datos["ciudad"],
        "telefono": datos.get("telefono", ""),
        "actualizado_en": _ahora(),
    }
    tabla().put_item(Item=item)   # PUT idempotente: crea o reemplaza
    logger.info("DynamoDB PUT dirección usuario=%s nombre=%s", username, nombre)
    return respuesta(200, _publico(item))


# ── #11 Wishlist ──────────────────────────────────────────────────────

def listar_wishlist(username: str) -> RespuestaApi:
    productos = [_publico(i) for i in _query_particion_usuario(username, "WISH#")]
    return respuesta(200, {
        "username": username,
        "productos": productos,
        "total": len(productos),
    })


def marcar_favorito(username: str, product_id: str) -> RespuestaApi:
    # A diferencia del carrito, aquí sí se valida el producto: la wishlist
    # no pasa por un checkout que lo revise después.
    if not _existe({"PK": claves.pk_producto(product_id), "SK": claves.sk_producto(product_id)}):
        return respuesta_error(404, f"Producto '{product_id}' no encontrado")

    item: Item = {
        "PK": claves.pk_usuario(username),
        "SK": claves.sk_wishlist(product_id),
        "tipo": "WISH",
        "productId": product_id,
        "agregado_en": _ahora(),
    }
    tabla().put_item(Item=item)   # repetir el clic no duplica
    logger.info("DynamoDB PUT wishlist usuario=%s producto=%s", username, product_id)
    return respuesta(200, _publico(item))


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    p: dict[str, str] = event.get("pathParameters") or {}
    body: str | None = event.get("body")

    logger.info("REQUEST %s %s %s", metodo, recurso, p)

    rutas: dict[tuple[str, str], Any] = {
        ("POST", "/users"): lambda: registrar_usuario(body),
        ("GET", "/users/{username}"): lambda: obtener_perfil(p["username"]),
        ("GET", "/users/{username}/addresses"): lambda: listar_direcciones(p["username"]),
        ("PUT", "/users/{username}/addresses/{nombre}"):
            lambda: guardar_direccion(p["username"], p["nombre"], body),
        ("DELETE", "/users/{username}/addresses/{nombre}"):
            lambda: _borrar(
                {"PK": claves.pk_usuario(p["username"]), "SK": claves.sk_direccion(p["nombre"])},
                f"La dirección '{p['nombre']}'",
            ),
        ("GET", "/users/{username}/wishlist"): lambda: listar_wishlist(p["username"]),
        ("PUT", "/users/{username}/wishlist/{productId}"):
            lambda: marcar_favorito(p["username"], p["productId"]),
        ("DELETE", "/users/{username}/wishlist/{productId}"):
            lambda: _borrar(
                {"PK": claves.pk_usuario(p["username"]), "SK": claves.sk_wishlist(p["productId"])},
                f"La marca de favorito de '{p['productId']}'",
            ),
    }
    accion = rutas.get((metodo, recurso))
    if accion is None:
        return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
    return accion()
