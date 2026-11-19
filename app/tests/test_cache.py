"""
test_cache.py
─────────────
Tests unitarios del módulo cache.py.
Verifica get / set / delete y el comportamiento
degradado cuando Redis no está disponible.
"""
import json
import sys
import os

import pytest
import fakeredis
import redis

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambdas/items"))


@pytest.fixture(autouse=True)
def parchear_redis(monkeypatch, redis_fake):
    import cache
    monkeypatch.setattr(cache, "_get_client", lambda: redis_fake)


# ── cache_set / cache_get ─────────────────────────────────────────────

def test_set_y_get_basico(redis_fake):
    import cache

    item = {"id": "x1", "nombre": "Test"}
    cache.cache_set("item#x1", item, ttl=60)

    resultado = cache.cache_get("item#x1")
    assert resultado == item


def test_get_clave_inexistente_retorna_none():
    import cache

    resultado = cache.cache_get("item#no-existe")
    assert resultado is None


def test_set_respeta_ttl(redis_fake):
    import cache

    cache.cache_set("item#ttl", {"v": 1}, ttl=10)
    ttl = redis_fake.ttl("item#ttl")
    # El TTL debe estar entre 1 y 10 segundos
    assert 1 <= ttl <= 10


def test_set_usa_ttl_de_entorno(monkeypatch, redis_fake):
    import cache
    # cache_set lee REDIS_TTL en cada llamada, no hace falta recargar el módulo
    monkeypatch.setenv("REDIS_TTL", "120")

    cache.cache_set("item#env", {"v": 2})
    ttl = redis_fake.ttl("item#env")
    assert 100 <= ttl <= 120


# ── cache_delete ──────────────────────────────────────────────────────

def test_delete_elimina_clave(redis_fake):
    import cache

    cache.cache_set("item#del", {"v": 3}, ttl=60)
    assert cache.cache_get("item#del") is not None

    cache.cache_delete("item#del")
    assert cache.cache_get("item#del") is None


def test_delete_clave_inexistente_no_falla():
    import cache
    # No debe lanzar excepción
    resultado = cache.cache_delete("item#no-existe")
    assert resultado is True


# ── Modo degradado (Redis no disponible) ──────────────────────────────

def test_get_cuando_redis_falla_retorna_none(monkeypatch):
    """Si Redis lanza RedisError, cache_get retorna None sin explotar."""
    import cache

    def redis_roto():
        r = fakeredis.FakeRedis()
        r.get = lambda k: (_ for _ in ()).throw(redis.ConnectionError("sin conexión"))
        return r

    monkeypatch.setattr(cache, "_get_client", redis_roto)
    resultado = cache.cache_get("item#cualquiera")
    assert resultado is None


def test_set_cuando_redis_falla_retorna_false(monkeypatch):
    """Si Redis lanza RedisError, cache_set retorna False sin explotar."""
    import cache

    def redis_roto():
        r = fakeredis.FakeRedis()
        r.setex = lambda *a, **k: (_ for _ in ()).throw(redis.ConnectionError("sin conexión"))
        return r

    monkeypatch.setattr(cache, "_get_client", redis_roto)
    resultado = cache.cache_set("item#cualquiera", {"v": 1})
    assert resultado is False


# ── Convención de nombres de claves ──────────────────────────────────

def test_cache_key_item_formato():
    import cache
    assert cache.cache_key_item("abc-123") == "item#abc-123"
