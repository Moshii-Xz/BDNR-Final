"""
Lambda cupones
──────────────
Validar un cupón de descuento (patrón de acceso #8).

Rutas soportadas:
  GET /coupons/{codigo}  → 200 con {valido, motivo, ...} si el cupón existe
                           (aunque esté vencido/desactivado), 404 si no existe.

Modelo (llaves en claves.py):
  Coupon: PK = SK = COUPON#<CODIGO>   → un GetItem, el código es la llave natural

Sin caché a propósito: se valida una vez por compra, no en cada clic, y
cachearlo arriesga aplicar un cupón que se acaba de desactivar.
Las reglas de vigencia están en cupones.py (las reutiliza el checkout).
"""
import logging
from typing import Any

from botocore.exceptions import ClientError

import claves
from cupones import evaluar_cupon, normalizar_codigo
from db import tabla
from respuestas import RespuestaApi, desde_dynamo, respuesta, respuesta_error

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)


def validar_cupon(codigo_raw: str) -> RespuestaApi:
    codigo: str = normalizar_codigo(codigo_raw)
    if not codigo:
        return respuesta_error(400, "Falta el código del cupón")

    try:
        resultado: dict[str, Any] = tabla().get_item(
            Key={"PK": claves.pk_cupon(codigo), "SK": claves.sk_cupon(codigo)}
        )
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al consultar la base de datos")

    item = resultado.get("Item")
    if not item:
        return respuesta_error(404, f"El cupón '{codigo}' no existe")

    valido, motivo = evaluar_cupon(item)
    cupon = desde_dynamo({k: v for k, v in item.items() if k not in ("PK", "SK", "tipo")})
    logger.info("Cupón %s valido=%s", codigo, valido)

    return respuesta(200, {**cupon, "valido": valido, "motivo": motivo})


def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    if recurso == "/coupons/{codigo}" and metodo == "GET":
        return validar_cupon(params.get("codigo", ""))
    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
