"""
cache.py
────────
Abstracción del cliente Redis.
Encapsula get / set / delete con manejo de errores,
de modo que si Redis no está disponible la Lambda
continúa funcionando (degraded mode → solo DynamoDB).
"""
import json
import logging
import os
from typing import Any, cast

import redis

logger: logging.Logger = logging.getLogger(__name__)


def _get_client() -> redis.Redis:
    """Crea y retorna un cliente Redis."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "redis"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        decode_responses=True,   # retorna str en lugar de bytes
    )


def cache_get(key: str) -> dict[str, Any] | None:
    """
    Busca una clave en Redis.
    Retorna el objeto deserializado o None si no existe / hay error.
    """
    try:
        client: redis.Redis = _get_client()
        # Con decode_responses=True redis-py retorna str, pero su firma
        # declara bytes | str; cast() lo indica al type checker.
        valor: str | None = cast(str | None, client.get(key))
        if valor:
            logger.info("CACHE HIT  key=%s", key)
            datos: dict[str, Any] = json.loads(valor)
            return datos
        logger.info("CACHE MISS key=%s", key)
        return None
    except redis.RedisError as e:
        logger.warning("Redis no disponible (get): %s", e)
        return None


def cache_set(key: str, valor: dict[str, Any], ttl: int | None = None) -> bool:
    """
    Guarda un objeto en Redis con TTL en segundos.
    Retorna True si se guardó correctamente.
    """
    if ttl is None:
        ttl = int(os.environ.get("REDIS_TTL", 300))
    try:
        client: redis.Redis = _get_client()
        client.setex(key, ttl, json.dumps(valor))
        logger.info("CACHE SET  key=%s ttl=%ss", key, ttl)
        return True
    except redis.RedisError as e:
        logger.warning("Redis no disponible (set): %s", e)
        return False


def cache_delete(key: str) -> bool:
    """
    Elimina una clave de Redis (útil al borrar o actualizar un ítem).
    Retorna True si se eliminó correctamente.
    """
    try:
        client: redis.Redis = _get_client()
        client.delete(key)
        logger.info("CACHE DEL  key=%s", key)
        return True
    except redis.RedisError as e:
        logger.warning("Redis no disponible (delete): %s", e)
        return False


def cache_key_item(item_id: str) -> str:
    """Convención de nombres de claves para ítems."""
    return f"item#{item_id}"
