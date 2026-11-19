"""
Lambda cupones
──────────────
Validar un cupón de descuento (patrón de acceso #8).

Rutas soportadas:
  GET  /coupons/{codigo} → 200 con {valido, motivo, ...} si el cupón existe
                           (aunque esté vencido/desactivado), 404 si no existe.
  POST /coupons          → Crear o reemplazar un cupón (administración)

Modelo (llaves en claves.py):
  Coupon: PK = SK = COUPON#<CODIGO>   → un GetItem, el código es la llave natural

Sin caché a propósito: se valida una vez por compra, no en cada clic, y
cachearlo arriesga aplicar un cupón que se acaba de desactivar.
Las reglas de vigencia están en cupones.py (las reutiliza el checkout).
"""
import json
import logging
import re
from datetime import date
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

import claves
from cupones import evaluar_cupon, normalizar_codigo
from db import tabla
from respuestas import RespuestaApi, cuerpo_json, desde_dynamo, respuesta, respuesta_error

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


PATRON_CODIGO = re.compile(r"^[A-Z0-9_-]{3,20}$")


def _es_entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def crear_cupon(body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    codigo = normalizar_codigo(str(datos.get("codigo") or ""))
    if not PATRON_CODIGO.match(codigo):
        return respuesta_error(400, "codigo debe tener 3-20 caracteres: letras, números, '_' o '-'")

    pct = datos.get("descuento_pct")
    if not _es_entero(pct) or not 1 <= pct <= 100:
        return respuesta_error(400, "descuento_pct debe ser un entero entre 1 y 100")

    activo = datos.get("activo", True)
    if not isinstance(activo, bool):
        return respuesta_error(400, "activo debe ser true o false")

    item: dict[str, Any] = {
        "PK": claves.pk_cupon(codigo),
        "SK": claves.sk_cupon(codigo),
        "tipo": "COUPON",
        "codigo": codigo,
        "descuento_pct": pct,
        "activo": activo,
    }

    fechas: dict[str, date] = {}
    for campo in ("vigente_desde", "vigente_hasta"):
        if datos.get(campo):
            try:
                fechas[campo] = date.fromisoformat(str(datos[campo]))
            except ValueError:
                return respuesta_error(400, f"{campo} debe tener formato YYYY-MM-DD")
            item[campo] = fechas[campo].isoformat()
    if len(fechas) == 2 and fechas["vigente_desde"] > fechas["vigente_hasta"]:
        return respuesta_error(400, "vigente_desde no puede ser posterior a vigente_hasta")

    minimo = datos.get("monto_minimo")
    if minimo is not None:
        if isinstance(minimo, bool) or not isinstance(minimo, (int, Decimal)) or minimo < 0:
            return respuesta_error(400, "monto_minimo debe ser un número mayor o igual a 0")
        item["monto_minimo"] = minimo

    # PutItem: si el código ya existía lo reemplaza (así se desactiva o se
    # corrige un cupón). No hay caché que invalidar: los cupones no se cachean.
    tabla().put_item(Item=item)
    logger.info("DynamoDB PUT cupón=%s", codigo)

    valido, motivo = evaluar_cupon(item)
    return respuesta(201, {**desde_dynamo({k: v for k, v in item.items()
                                           if k not in ("PK", "SK", "tipo")}),
                           "valido": valido, "motivo": motivo})


def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    if recurso == "/coupons/{codigo}" and metodo == "GET":
        return validar_cupon(params.get("codigo", ""))
    if recurso == "/coupons" and metodo == "POST":
        return crear_cupon(event.get("body"))
    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
