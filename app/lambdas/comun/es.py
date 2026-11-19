"""
─────
Abstracción del cliente Elasticsearch.

Por qué existe: el patrón de acceso "buscar productos por una palabra
dentro del nombre, la marca o la descripción" no se resuelve con una
llave de DynamoDB. DynamoDB sigue siendo la fuente de verdad del
producto; Elasticsearch solo indexa una copia de los campos de texto
para resolver ese patrón de búsqueda libre.

Igual que con Redis, si Elasticsearch no está disponible las funciones
de escritura no deben tumbar la Lambda que crea/edita el producto.
"""
import logging
import os
from typing import Any

from elasticsearch import Elasticsearch
# En elasticsearch-py 8.x ya no existe ElasticsearchException: los errores
# del servidor son ApiError y los de red/conexión TransportError.
from elasticsearch import ApiError, TransportError

ErroresES = (ApiError, TransportError)

logger: logging.Logger = logging.getLogger(__name__)

INDICE_PRODUCTOS = "productos"

MAPEO_INDICE_PRODUCTOS: dict[str, Any] = {
    "mappings": {
        "properties": {
            "productId": {"type": "keyword"},
            "nombre": {"type": "text"},
            "marca": {"type": "text"},
            "categoria": {"type": "keyword"},
            "descripcion": {"type": "text"},
            "precio": {"type": "double"},
        }
    }
}


def _get_client() -> Elasticsearch:
    host: str = os.environ.get("ES_HOST", "elasticsearch")
    port: str = os.environ.get("ES_PORT", "9200")
    return Elasticsearch(f"http://{host}:{port}", request_timeout=3)


def asegurar_indice() -> None:
    """Crea el índice de productos si todavía no existe (idempotente)."""
    try:
        client = _get_client()
        if not client.indices.exists(index=INDICE_PRODUCTOS):
            client.indices.create(index=INDICE_PRODUCTOS, mappings=MAPEO_INDICE_PRODUCTOS["mappings"])
            logger.info("Índice '%s' creado en Elasticsearch", INDICE_PRODUCTOS)
    except ErroresES as e:
        logger.warning("Elasticsearch no disponible (asegurar_indice): %s", e)


def indexar_producto(producto: dict[str, Any]) -> bool:
    """
    Indexa (o reemplaza) el documento de un producto.
    Se llama justo después de escribir el producto en DynamoDB.
    """
    try:
        client = _get_client()
        client.index(
            index=INDICE_PRODUCTOS,
            id=producto["id"],
            document={
                "productId": producto["id"],
                "nombre": producto.get("nombre", ""),
                "marca": producto.get("marca", ""),
                "categoria": producto.get("categoria", ""),
                "descripcion": producto.get("descripcion", ""),
                "precio": producto.get("precio", 0),
            },
        )
        return True
    except ErroresES as e:
        logger.warning("Elasticsearch no disponible (indexar_producto): %s", e)
        return False


def eliminar_producto_indice(product_id: str) -> bool:
    try:
        client = _get_client()
        # 404 = el documento ya no estaba indexado; no es un error
        client.options(ignore_status=404).delete(index=INDICE_PRODUCTOS, id=product_id)
        return True
    except ErroresES as e:
        logger.warning("Elasticsearch no disponible (eliminar_producto_indice): %s", e)
        return False


def buscar_productos(texto: str, limite: int = 20) -> list[dict[str, Any]]:
    """
    Búsqueda libre: coincide por palabra dentro de nombre, marca o
    descripción (multi_match con fuzziness para tolerar errores de
    tipeo). nombre pesa el doble (boost).
    """
    try:
        client = _get_client()
        resultado = client.search(
            index=INDICE_PRODUCTOS,
            size=limite,
            query={
                "multi_match": {
                    "query": texto,
                    "fields": ["nombre^2", "marca", "descripcion"],
                    "fuzziness": "AUTO",
                }
            },
        )
        return [hit["_source"] for hit in resultado["hits"]["hits"]]
    except ErroresES as e:
        logger.warning("Elasticsearch no disponible (buscar_productos): %s", e)
        return []