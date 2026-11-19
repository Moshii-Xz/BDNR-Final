"""
Lambda /items
─────────────
Maneja las operaciones CRUD sobre ítems.

Rutas soportadas:
  POST   /items          → Crear ítem  (Write-Through: DynamoDB + Redis)
  GET    /items/{id}     → Obtener ítem (Cache-Aside: Redis → DynamoDB)
  DELETE /items/{id}     → Eliminar ítem (invalida la clave en Redis)

Modelo de datos en DynamoDB (Single Table Design):
  PK = "ITEM#<id>"
  SK = "ITEM#<id>"
  + atributos del ítem
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeAlias

import boto3
from boto3.resources.base import ServiceResource
from botocore.exceptions import ClientError

from cache import cache_get, cache_set, cache_delete, cache_key_item

# Respuesta en formato proxy de API Gateway
RespuestaApi: TypeAlias = dict[str, Any]
# Ítem tal como se guarda en DynamoDB / Redis
Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cliente DynamoDB — boto3 lee AWS_ENDPOINT_URL del entorno
# para redirigir al emulador local automáticamente.
dynamodb: ServiceResource = boto3.resource("dynamodb")
TABLE_NAME: str = os.environ["TABLE_NAME"]


def _tabla() -> Any:
    # boto3 genera la clase Table en tiempo de ejecución; sin los stubs
    # de mypy-boto3-dynamodb no hay un tipo estático más preciso.
    return dynamodb.Table(TABLE_NAME)


# ── Helpers ───────────────────────────────────────────────────────────

def _desde_dynamo(valor: Any) -> Any:
    """
    DynamoDB devuelve los números como Decimal, que json.dumps no serializa.
    Los convierte a int (si son enteros) o float, recursivamente.
    """
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    if isinstance(valor, dict):
        return {k: _desde_dynamo(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_desde_dynamo(v) for v in valor]
    return valor


def _respuesta(status: int, body: dict[str, Any]) -> RespuestaApi:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _respuesta_error(status: int, mensaje: str) -> RespuestaApi:
    return _respuesta(status, {"error": mensaje})


# ── Handlers por método ───────────────────────────────────────────────

def crear_item(body_raw: str | None) -> RespuestaApi:
    """
    POST /items
    Crea un ítem en DynamoDB y lo guarda en Redis (Write-Through).
    """
    try:
        # DynamoDB no acepta float: los decimales se leen como Decimal
        datos: dict[str, Any] = json.loads(body_raw or "{}", parse_float=Decimal)
    except json.JSONDecodeError:
        return _respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    item_id: str = datos.get("id") or str(uuid.uuid4())
    ahora: str = datetime.now(timezone.utc).isoformat()

    item: Item = {
        "PK": f"ITEM#{item_id}",
        "SK": f"ITEM#{item_id}",
        "id":         item_id,
        "nombre":     datos.get("nombre", "Sin nombre"),
        "precio":     datos.get("precio", 0),
        "descripcion": datos.get("descripcion", ""),
        "creado_en":  ahora,
        "actualizado_en": ahora,
    }

    # 1. Guardar en DynamoDB
    _tabla().put_item(Item=item)
    logger.info("DynamoDB PUT item_id=%s", item_id)

    # 2. Write-Through: guardar también en Redis
    item = _desde_dynamo(item)
    cache_set(cache_key_item(item_id), item)

    return _respuesta(201, item)


def obtener_item(item_id: str) -> RespuestaApi:
    """
    GET /items/{id}
    Patrón Cache-Aside:
      1. Busca en Redis  → si hay hit, retorna directo
      2. Si miss        → lee DynamoDB, guarda en Redis, retorna
    """
    clave: str = cache_key_item(item_id)

    # 1. Verificar Redis
    cached: Item | None = cache_get(clave)
    if cached:
        return _respuesta(200, {**cached, "_fuente": "cache"})

    # 2. Leer DynamoDB
    try:
        respuesta: dict[str, Any] = _tabla().get_item(
            Key={"PK": f"ITEM#{item_id}", "SK": f"ITEM#{item_id}"}
        )
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return _respuesta_error(500, "Error al consultar la base de datos")

    item: Item | None = respuesta.get("Item")
    if not item:
        return _respuesta_error(404, f"Ítem '{item_id}' no encontrado")

    logger.info("DynamoDB GET item_id=%s", item_id)

    # 3. Guardar en Redis para próximas consultas
    item = _desde_dynamo(item)
    cache_set(clave, item)

    return _respuesta(200, {**item, "_fuente": "dynamodb"})


def eliminar_item(item_id: str) -> RespuestaApi:
    """
    DELETE /items/{id}
    Elimina el ítem de DynamoDB e invalida la clave en Redis.
    """
    try:
        _tabla().delete_item(
            Key={"PK": f"ITEM#{item_id}", "SK": f"ITEM#{item_id}"},
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _respuesta_error(404, f"Ítem '{item_id}' no encontrado")
        logger.error("Error DynamoDB: %s", e)
        return _respuesta_error(500, "Error al eliminar el ítem")

    logger.info("DynamoDB DELETE item_id=%s", item_id)

    # Invalidar caché
    cache_delete(cache_key_item(item_id))

    return _respuesta(200, {"mensaje": f"Ítem '{item_id}' eliminado"})


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    """
    Handler principal de la Lambda.
    API Gateway inyecta el método HTTP y los parámetros de ruta.
    """
    metodo: str = event.get("httpMethod", "GET")
    params: dict[str, str] = event.get("pathParameters") or {}
    item_id: str | None = params.get("id")

    logger.info("REQUEST %s /items%s", metodo, f"/{item_id}" if item_id else "")

    if metodo == "POST":
        return crear_item(event.get("body", "{}"))

    if metodo == "GET" and item_id:
        return obtener_item(item_id)

    if metodo == "DELETE" and item_id:
        return eliminar_item(item_id)

    return _respuesta_error(405, f"Método '{metodo}' no soportado en esta ruta")
