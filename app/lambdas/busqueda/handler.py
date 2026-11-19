"""
Lambda busqueda
───────────────
Búsqueda libre de productos por texto (patrón de acceso #6).

Rutas soportadas:
  GET /search?q=<texto>&limite=<n>  → productos cuyo nombre, marca o
                                      descripción coinciden con el texto

No usa DynamoDB: buscar una palabra dentro de un texto no es una búsqueda
por igualdad de llave, así que se resuelve contra el índice espejo
"productos" de Elasticsearch (multi_match con fuzziness, nombre con boost x2).
DynamoDB sigue siendo la fuente de verdad: la Lambda productos escribe ese
espejo al crear/eliminar.

Modo degradado: si Elasticsearch no responde, se devuelve una lista vacía
(200) en vez de un error; el resto del ecommerce no depende de ES.
Sin Redis encima: ES ya tiene su propia caché de consultas.
"""
import logging
from typing import Any

from es import buscar_productos
from respuestas import RespuestaApi, respuesta, respuesta_error

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

LIMITE_POR_DEFECTO: int = 20
LIMITE_MAXIMO: int = 50


def buscar(query: dict[str, str]) -> RespuestaApi:
    texto: str = (query.get("q") or "").strip()
    if not texto:
        return respuesta_error(400, "Falta el parámetro q con el texto a buscar")

    try:
        limite = max(1, min(int(query.get("limite") or LIMITE_POR_DEFECTO), LIMITE_MAXIMO))
    except ValueError:
        return respuesta_error(400, "limite debe ser un número entero")

    resultados = buscar_productos(texto, limite)
    logger.info("Búsqueda q=%r resultados=%d", texto, len(resultados))
    return respuesta(200, {
        "q": texto,
        "resultados": resultados,
        "total": len(resultados),
        "_fuente": "elasticsearch",
    })


def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")

    logger.info("REQUEST %s %s", metodo, recurso)

    if (metodo, recurso) == ("GET", "/search"):
        return buscar(event.get("queryStringParameters") or {})
    return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
