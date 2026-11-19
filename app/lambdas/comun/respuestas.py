"""
respuestas
──────────
(No se llama http.py para no tapar el módulo http de la librería
estándar, que usan botocore/urllib3: las Lambdas importan planos.)

Helpers de formato para las respuestas del API Gateway (proxy integration)
y conversión de los tipos que devuelve DynamoDB (Decimal) a tipos nativos
de Python serializables en JSON.
"""
import json
from decimal import Decimal
from typing import Any, TypeAlias

RespuestaApi: TypeAlias = dict[str, Any]


def desde_dynamo(valor: Any) -> Any:
    """
    DynamoDB devuelve los números como Decimal, que json.dumps no serializa.
    Los convierte a int (si son enteros) o float, recursivamente.
    """
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    if isinstance(valor, dict):
        return {k: desde_dynamo(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [desde_dynamo(v) for v in valor]
    return valor


def respuesta(status: int, body: dict[str, Any]) -> RespuestaApi:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def respuesta_error(status: int, mensaje: str) -> RespuestaApi:
    return respuesta(status, {"error": mensaje})


def cuerpo_json(body_raw: str | None, parse_float: Any = Decimal) -> dict[str, Any]:
    """
    Parsea el body de la petición. Lanza json.JSONDecodeError si no es
    JSON válido (el handler que llama decide cómo responder, normalmente
    con respuesta_error(400, "...")).
    """
    return json.loads(body_raw or "{}", parse_float=parse_float)