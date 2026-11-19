"""
Lambda /health
──────────────
Health check del servicio.
Verifica conectividad con DynamoDB, Redis y Elasticsearch
y retorna el estado de cada componente.

Rutas soportadas:
  GET    /health         → Estado de DynamoDB, Redis y Elasticsearch
                           (200 si todo está ok, 503 si alguno falla)
"""
import json
import logging
import os
from typing import Any, TypeAlias

import boto3
import redis
from elasticsearch import Elasticsearch
from boto3.resources.base import ServiceResource
from botocore.exceptions import ClientError

# Respuesta en formato proxy de API Gateway
RespuestaApi: TypeAlias = dict[str, Any]
# Estado de un componente: {"estado": "ok" | "error", ...}
EstadoComponente: TypeAlias = dict[str, str]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb: ServiceResource = boto3.resource("dynamodb")
TABLE_NAME: str = os.environ["TABLE_NAME"]


def _check_dynamodb() -> EstadoComponente:
    try:
        tabla: Any = dynamodb.Table(TABLE_NAME)
        tabla.load()   # describe la tabla; falla si no existe
        return {"estado": "ok", "tabla": TABLE_NAME}
    except ClientError as e:
        return {"estado": "error", "detalle": str(e)}
    except Exception as e:
        return {"estado": "error", "detalle": str(e)}


def _check_redis() -> EstadoComponente:
    host: str = os.environ.get("REDIS_HOST", "redis")
    try:
        cliente: redis.Redis = redis.Redis(
            host=host,
            port=int(os.environ.get("REDIS_PORT", 6379)),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        cliente.ping()
        return {"estado": "ok", "host": host}
    except redis.RedisError as e:
        return {"estado": "error", "detalle": str(e)}


def _check_elasticsearch() -> EstadoComponente:
    host: str = os.environ.get("ES_HOST", "elasticsearch")
    port: str = os.environ.get("ES_PORT", "9200")
    try:
        cliente = Elasticsearch(f"http://{host}:{port}", request_timeout=2)
        salud = cliente.cluster.health()
        # Un nodo único queda en "yellow" (no hay dónde poner réplicas): es normal
        return {"estado": "ok", "host": host, "cluster": str(salud["status"])}
    except Exception as e:   # el cliente lanza varios tipos según el fallo de red
        return {"estado": "error", "detalle": str(e)}


def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    logger.info("Health check solicitado")

    db_status: EstadoComponente    = _check_dynamodb()
    redis_status: EstadoComponente = _check_redis()
    es_status: EstadoComponente    = _check_elasticsearch()

    todo_ok: bool = (
        db_status["estado"] == "ok"
        and redis_status["estado"] == "ok"
        and es_status["estado"] == "ok"
    )

    return {
        "statusCode": 200 if todo_ok else 503,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "estado":   "ok" if todo_ok else "degradado",
            "dynamodb": db_status,
            "redis":    redis_status,
            "elasticsearch": es_status,
        }),
    }
