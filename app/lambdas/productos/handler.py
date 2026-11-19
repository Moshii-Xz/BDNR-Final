"""
Lambda productos
────────────────
Catálogo del ecommerce (patrones de acceso #1 a #5) y ranking de más vistos.

Rutas soportadas (se despacha por event["resource"], la plantilla de la ruta):
  GET    /products                         → #3 Catálogo completo (Scan, baja frecuencia)
  POST   /products                         → Crear/reemplazar producto (+ espejo en Elasticsearch)
  GET    /products/ranking                 → Más vistos (Sorted Set, vive solo en Redis)
  GET    /products/{id}                    → #1 Detalle (Cache-Aside)
  DELETE /products/{id}                    → Eliminar producto y sus reseñas
  GET    /products/{id}/reviews            → #4 Reseñas, de la más reciente a la más antigua
  POST   /products/{id}/reviews            → #4 Agregar reseña
  GET    /categories/{categoria}/products  → #2 Productos de una categoría (GSI1)
  GET    /brands/{marca}/products          → #5 Productos de una marca, ?categoria= opcional (GSI2)

Modelo (llaves en claves.py):
  Product: PK = SK = PRODUCT#<id>
           GSI1PK = CAT#<categoria>   GSI1SK = PRODUCT#<id>
           GSI2PK = BRAND#<marca>     GSI2SK = CAT#<categoria>#PRODUCT#<id>
  Review:  PK = PRODUCT#<id>          SK = REVIEW#<fechaISO>#<username>
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeAlias

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

import claves
from cache import (
    cache_delete,
    cache_get,
    cache_key_producto,
    cache_set,
    incrementar_contador_ranking,
    top_ranking,
)
from db import tabla
from es import eliminar_producto_indice, indexar_producto
from respuestas import RespuestaApi, cuerpo_json, desde_dynamo, respuesta, respuesta_error

Item: TypeAlias = dict[str, Any]

logger: logging.Logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Atributos internos del Single Table Design que no se exponen en el API
_ATRIBUTOS_LLAVE: frozenset[str] = frozenset(
    {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "tipo"}
)
_CAMPOS_OBLIGATORIOS: tuple[str, ...] = ("nombre", "categoria", "marca", "precio")


# ── Helpers ───────────────────────────────────────────────────────────

def _publico(item: Item) -> Item:
    """Quita las llaves PK/SK/GSI y convierte Decimal → int/float."""
    return desde_dynamo({k: v for k, v in item.items() if k not in _ATRIBUTOS_LLAVE})


def _llave_producto(product_id: str) -> dict[str, str]:
    return {"PK": claves.pk_producto(product_id), "SK": claves.sk_producto(product_id)}


def _query_todo(**kwargs: Any) -> list[Item]:
    """Query paginando con LastEvaluatedKey (DynamoDB corta en 1 MB)."""
    items: list[Item] = []
    while True:
        pagina: dict[str, Any] = tabla().query(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            return items
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]


# ── #3 Catálogo completo ──────────────────────────────────────────────

def listar_productos() -> RespuestaApi:
    """
    Única lectura del proyecto con Scan (decisión documentada): es la de
    menor frecuencia y el catálogo del laboratorio es pequeño, así que no se
    justifica un tercer índice. El filtro por SK excluye las reseñas, que
    comparten PK=PRODUCT#<id>.
    """
    filtro = Attr("PK").begins_with("PRODUCT#") & Attr("SK").begins_with("PRODUCT#")
    items: list[Item] = []
    kwargs: dict[str, Any] = {"FilterExpression": filtro}
    while True:
        pagina: dict[str, Any] = tabla().scan(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            break
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]

    productos = [_publico(i) for i in items]
    return respuesta(200, {"productos": productos, "total": len(productos)})


# ── Crear producto ────────────────────────────────────────────────────

def crear_producto(body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    faltantes = [c for c in _CAMPOS_OBLIGATORIOS if datos.get(c) in (None, "")]
    if faltantes:
        return respuesta_error(400, f"Faltan campos obligatorios: {', '.join(faltantes)}")
    precio = datos["precio"]
    if isinstance(precio, bool) or not isinstance(precio, (int, Decimal)) or precio < 0:
        return respuesta_error(400, "precio debe ser un número mayor o igual a 0")

    product_id: str = str(datos.get("id") or uuid.uuid4())
    categoria: str = str(datos["categoria"])
    marca: str = str(datos["marca"])
    ahora: str = datetime.now(timezone.utc).isoformat()

    item: Item = {
        **_llave_producto(product_id),
        # Las mismas llaves de producto reindexadas por categoría (#2) y marca (#5)
        "GSI1PK": claves.gsi1_pk_categoria(categoria),
        "GSI1SK": claves.gsi1_sk_producto(product_id),
        "GSI2PK": claves.gsi2_pk_marca(marca),
        "GSI2SK": claves.gsi2_sk_marca(categoria, product_id),
        "tipo": "PRODUCT",
        "id": product_id,
        "nombre": datos["nombre"],
        "categoria": categoria,
        "marca": marca,
        "precio": datos["precio"],
        "descripcion": datos.get("descripcion", ""),
        "stock": datos.get("stock", 0),
        "actualizado_en": ahora,
    }

    # 1. DynamoDB es la fuente de verdad
    tabla().put_item(Item=item)
    logger.info("DynamoDB PUT producto=%s", product_id)

    producto: Item = _publico(item)

    # 2. Cache-Aside: si el producto ya existía, su copia en Redis queda vieja;
    #    se invalida y el próximo GET la vuelve a cargar desde DynamoDB.
    cache_delete(cache_key_producto(product_id))

    # 3. Espejo en Elasticsearch para la búsqueda libre (#6). Si ES no
    #    responde, el producto igual queda creado (modo degradado).
    indexado: bool = indexar_producto(producto)

    return respuesta(201, {**producto, "_indexado_busqueda": indexado})


# ── #1 Detalle (Cache-Aside) ──────────────────────────────────────────

def obtener_producto(product_id: str) -> RespuestaApi:
    clave: str = cache_key_producto(product_id)

    # 1. Redis
    cached: Item | None = cache_get(clave)
    if cached:
        incrementar_contador_ranking(product_id)
        return respuesta(200, {**cached, "_fuente": "cache"})

    # 2. Miss → DynamoDB (GetItem, la lectura más barata)
    try:
        resultado: dict[str, Any] = tabla().get_item(Key=_llave_producto(product_id))
    except ClientError as e:
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al consultar la base de datos")

    item: Item | None = resultado.get("Item")
    if not item:
        return respuesta_error(404, f"Producto '{product_id}' no encontrado")

    # 3. Guardar en Redis con el TTL corto por defecto (REDIS_TTL)
    producto: Item = _publico(item)
    cache_set(clave, producto)
    incrementar_contador_ranking(product_id)

    return respuesta(200, {**producto, "_fuente": "dynamodb"})


# ── Eliminar producto ─────────────────────────────────────────────────

def eliminar_producto(product_id: str) -> RespuestaApi:
    """
    Borra el producto y, como sus reseñas comparten PK=PRODUCT#<id>, también
    toda esa partición (si no, quedarían reseñas huérfanas visibles en #4).
    """
    try:
        tabla().delete_item(
            Key=_llave_producto(product_id),
            ConditionExpression="attribute_exists(PK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respuesta_error(404, f"Producto '{product_id}' no encontrado")
        logger.error("Error DynamoDB: %s", e)
        return respuesta_error(500, "Error al eliminar el producto")

    resenas: list[Item] = _query_todo(
        KeyConditionExpression=Key("PK").eq(claves.pk_producto(product_id))
        & Key("SK").begins_with("REVIEW#"),
        ProjectionExpression="PK, SK",
    )
    with tabla().batch_writer() as lote:
        for r in resenas:
            lote.delete_item(Key={"PK": r["PK"], "SK": r["SK"]})

    logger.info("DynamoDB DELETE producto=%s (+%d reseñas)", product_id, len(resenas))

    cache_delete(cache_key_producto(product_id))
    eliminar_producto_indice(product_id)

    return respuesta(200, {
        "mensaje": f"Producto '{product_id}' eliminado",
        "resenas_eliminadas": len(resenas),
    })


# ── #2 Por categoría (GSI1) ───────────────────────────────────────────

def productos_por_categoria(categoria: str) -> RespuestaApi:
    items = _query_todo(
        IndexName="GSI1",
        # begins_with en la SK: GSI1 también guarda órdenes (ORDER#...),
        # aunque con otra PK; se deja explícito que aquí solo van productos.
        KeyConditionExpression=Key("GSI1PK").eq(claves.gsi1_pk_categoria(categoria))
        & Key("GSI1SK").begins_with("PRODUCT#"),
    )
    productos = [_publico(i) for i in items]
    return respuesta(200, {"categoria": categoria, "productos": productos, "total": len(productos)})


# ── #5 Por marca y, opcionalmente, categoría (GSI2) ───────────────────

def productos_por_marca(marca: str, categoria: str | None) -> RespuestaApi:
    condicion = Key("GSI2PK").eq(claves.gsi2_pk_marca(marca))
    if categoria:
        # La categoría está al inicio de la SK: el mismo índice sirve para
        # "marca sola" y "marca + categoría".
        condicion = condicion & Key("GSI2SK").begins_with(f"CAT#{categoria}#")
    items = _query_todo(IndexName="GSI2", KeyConditionExpression=condicion)
    productos = [_publico(i) for i in items]
    return respuesta(200, {
        "marca": marca,
        "categoria": categoria,
        "productos": productos,
        "total": len(productos),
    })


# ── #4 Reseñas ────────────────────────────────────────────────────────

def listar_resenas(product_id: str) -> RespuestaApi:
    items = _query_todo(
        KeyConditionExpression=Key("PK").eq(claves.pk_producto(product_id))
        & Key("SK").begins_with("REVIEW#"),
        # La fecha va al frente de la SK: orden descendente = más reciente primero
        ScanIndexForward=False,
    )
    resenas = [_publico(i) for i in items]
    return respuesta(200, {"productId": product_id, "resenas": resenas, "total": len(resenas)})


def crear_resena(product_id: str, body_raw: str | None) -> RespuestaApi:
    try:
        datos: dict[str, Any] = cuerpo_json(body_raw)
    except json.JSONDecodeError:
        return respuesta_error(400, "El cuerpo de la petición no es JSON válido")

    username = datos.get("username")
    calificacion = datos.get("calificacion")
    if not username:
        return respuesta_error(400, "Falta el campo obligatorio: username")
    if not isinstance(calificacion, int) or isinstance(calificacion, bool) or not 1 <= calificacion <= 5:
        return respuesta_error(400, "calificacion debe ser un entero entre 1 y 5")

    # El producto debe existir (GetItem barato antes de escribir)
    if "Item" not in tabla().get_item(Key=_llave_producto(product_id), ProjectionExpression="PK"):
        return respuesta_error(404, f"Producto '{product_id}' no encontrado")

    # Fecha generada en el servidor, no por el cliente (evita relojes desfasados)
    fecha: str = datetime.now(timezone.utc).isoformat()
    item: Item = {
        "PK": claves.pk_producto(product_id),
        "SK": claves.sk_review(fecha, str(username)),
        "tipo": "REVIEW",
        "productId": product_id,
        "username": str(username),
        "calificacion": calificacion,
        "comentario": datos.get("comentario", ""),
        "fecha": fecha,
    }
    tabla().put_item(Item=item)
    logger.info("DynamoDB PUT reseña producto=%s usuario=%s", product_id, username)

    return respuesta(201, _publico(item))


# ── Ranking (Sorted Set nativo de Redis) ──────────────────────────────

def ranking_mas_vistos(limite_raw: str | None) -> RespuestaApi:
    try:
        limite = max(1, min(int(limite_raw or 10), 50))
    except ValueError:
        return respuesta_error(400, "limite debe ser un número entero")
    return respuesta(200, {"ranking": top_ranking(limite), "_fuente": "redis"})


# ── Entrada principal ─────────────────────────────────────────────────

def main(event: dict[str, Any], context: Any) -> RespuestaApi:
    metodo: str = event.get("httpMethod", "GET")
    recurso: str = event.get("resource", "")
    params: dict[str, str] = event.get("pathParameters") or {}
    query: dict[str, str] = event.get("queryStringParameters") or {}
    body: str | None = event.get("body")

    logger.info("REQUEST %s %s %s", metodo, recurso, params)

    rutas: dict[tuple[str, str], Any] = {
        ("GET", "/products"): lambda: listar_productos(),
        ("POST", "/products"): lambda: crear_producto(body),
        ("GET", "/products/ranking"): lambda: ranking_mas_vistos(query.get("limite")),
        ("GET", "/products/{id}"): lambda: obtener_producto(params["id"]),
        ("DELETE", "/products/{id}"): lambda: eliminar_producto(params["id"]),
        ("GET", "/products/{id}/reviews"): lambda: listar_resenas(params["id"]),
        ("POST", "/products/{id}/reviews"): lambda: crear_resena(params["id"], body),
        ("GET", "/categories/{categoria}/products"):
            lambda: productos_por_categoria(params["categoria"]),
        ("GET", "/brands/{marca}/products"):
            lambda: productos_por_marca(params["marca"], query.get("categoria")),
    }

    accion = rutas.get((metodo, recurso))
    if accion is None:
        return respuesta_error(405, f"Método '{metodo}' no soportado en '{recurso}'")
    return accion()
