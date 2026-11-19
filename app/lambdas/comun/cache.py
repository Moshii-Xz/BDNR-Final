"""
────────
Abstracción del cliente Redis para todo el ecommerce.

Reutiliza la idea del proyecto base (lambdas/items/cache.py): si Redis
no está disponible, las funciones retornan None/False en vez de lanzar
una excepción, para que las Lambdas sigan funcionando contra DynamoDB
solamente.
"""
import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import Any, cast

import redis

logger: logging.Logger = logging.getLogger(__name__)

# Bogotá es UTC-5 todo el año (no tiene horario de verano)
ZONA_BOGOTA = timezone(timedelta(hours=-5))


def _get_client() -> redis.Redis:
    """Crea y retorna un cliente Redis."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "redis"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        decode_responses=True,
        socket_connect_timeout=2,
    )


# ── Cache-Aside / Write-Through genérico ─────

def cache_get(key: str) -> dict[str, Any] | None:
    try:
        client: redis.Redis = _get_client()
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
    if ttl is None:
        ttl = int(os.environ.get("REDIS_TTL", 300))
    try:
        client: redis.Redis = _get_client()
        client.setex(key, max(ttl, 1), json.dumps(valor))
        logger.info("CACHE SET  key=%s ttl=%ss", key, ttl)
        return True
    except redis.RedisError as e:
        logger.warning("Redis no disponible (set): %s", e)
        return False


def cache_delete(key: str) -> bool:
    try:
        client: redis.Redis = _get_client()
        client.delete(key)
        logger.info("CACHE DEL  key=%s", key)
        return True
    except redis.RedisError as e:
        logger.warning("Redis no disponible (delete): %s", e)
        return False


# ── Convenciones de nombres de clave por entidad ──────────────────────

def cache_key_producto(product_id: str) -> str:
    return f"producto#{product_id}"


def cache_key_orden(order_id: str) -> str:
    return f"orden#{order_id}"


def cache_key_promocion(fecha: str) -> str:
    return f"promo#{fecha}"


# ── Write-Through con TTL "hasta medianoche" (promoción del día) ─────

def segundos_hasta_medianoche_bogota(ahora: datetime | None = None) -> int:
    """
    Calcula cuántos segundos faltan para la medianoche de Bogotá.
    Usado como TTL de la promoción del día: así Redis la vence
    exactamente cuando deja de ser válida, sin necesitar un job de
    limpieza aparte. Mínimo 1 segundo para evitar un TTL de 0/negativo
    si el cálculo se hace justo a la medianoche.
    """
    ahora = (ahora or datetime.now(ZONA_BOGOTA)).astimezone(ZONA_BOGOTA)
    medianoche_siguiente = datetime.combine(
        ahora.date() + timedelta(days=1), time.min, tzinfo=ZONA_BOGOTA
    )
    return max(int((medianoche_siguiente - ahora).total_seconds()), 1)


def cache_set_hasta_medianoche(key: str, valor: dict[str, Any]) -> bool:
    """Write-Through: usado justo después de escribir en DynamoDB."""
    return cache_set(key, valor, ttl=segundos_hasta_medianoche_bogota())


# ── Ranking de productos más vistos (Sorted Set nativo de Redis) ─────
# No es Cache-Aside de DynamoDB: es un dato que solo existe en Redis,
# aproximado y no crítico (si Redis se reinicia, el ranking se pierde
# y vuelve a empezar desde cero; no afecta el catálogo real).

CLAVE_RANKING_VISTAS = "ranking:productos:vistas"


def incrementar_contador_ranking(product_id: str) -> None:
    try:
        client: redis.Redis = _get_client()
        client.zincrby(CLAVE_RANKING_VISTAS, 1, product_id)
    except redis.RedisError as e:
        logger.warning("Redis no disponible (zincrby ranking): %s", e)


def top_ranking(limite: int = 10) -> list[dict[str, Any]]:
    try:
        client: redis.Redis = _get_client()
        pares: list[tuple[str, float]] = client.zrevrange(
            CLAVE_RANKING_VISTAS, 0, limite - 1, withscores=True
        )
        return [{"productId": pid, "vistas": int(puntos)} for pid, puntos in pares]
    except redis.RedisError as e:
        logger.warning("Redis no disponible (zrevrange ranking): %s", e)
        return []