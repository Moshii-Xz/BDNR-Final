"""
test_health.py
──────────────
Tests de la Lambda health: 200 solo si DynamoDB, Redis y Elasticsearch
responden; 503 ("degradado") si cualquiera de los tres falla.
"""
import json

import pytest
import redis


class _RedisOk:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


class _RedisCaido(_RedisOk):
    def ping(self):
        raise redis.ConnectionError("sin conexión")


class _Cluster:
    def __init__(self, falla: bool):
        self._falla = falla

    def health(self):
        if self._falla:
            raise ConnectionError("Elasticsearch no responde")
        return {"status": "yellow"}   # nodo único: lo normal en local


def _es(falla: bool):
    class _Elasticsearch:
        def __init__(self, *args, **kwargs):
            self.cluster = _Cluster(falla)
    return _Elasticsearch


@pytest.fixture
def health(tabla_dynamodb, cargar_lambda, monkeypatch):
    modulo = cargar_lambda("health")
    monkeypatch.setattr(modulo.redis, "Redis", _RedisOk)
    monkeypatch.setattr(modulo, "Elasticsearch", _es(falla=False))
    return modulo


def _llamar(modulo):
    r = modulo.main({"httpMethod": "GET", "resource": "/health"}, None)
    return r["statusCode"], json.loads(r["body"])


def test_todo_ok(health):
    status, cuerpo = _llamar(health)
    assert status == 200
    assert cuerpo["estado"] == "ok"
    assert cuerpo["dynamodb"]["estado"] == "ok"
    assert cuerpo["redis"]["estado"] == "ok"
    assert cuerpo["elasticsearch"] == {"estado": "ok", "host": "elasticsearch", "cluster": "yellow"}


def test_redis_caido_degrada(health, monkeypatch):
    monkeypatch.setattr(health.redis, "Redis", _RedisCaido)
    status, cuerpo = _llamar(health)
    assert status == 503
    assert cuerpo["estado"] == "degradado"
    assert cuerpo["redis"]["estado"] == "error"
    assert cuerpo["dynamodb"]["estado"] == "ok"


def test_elasticsearch_caido_degrada(health, monkeypatch):
    monkeypatch.setattr(health, "Elasticsearch", _es(falla=True))
    status, cuerpo = _llamar(health)
    assert status == 503
    assert cuerpo["elasticsearch"]["estado"] == "error"
    assert "no responde" in cuerpo["elasticsearch"]["detalle"]
    assert cuerpo["redis"]["estado"] == "ok"


def test_tabla_inexistente_degrada(health, monkeypatch):
    monkeypatch.setattr(health, "TABLE_NAME", "no-existe")
    status, cuerpo = _llamar(health)
    assert status == 503
    assert cuerpo["dynamodb"]["estado"] == "error"
