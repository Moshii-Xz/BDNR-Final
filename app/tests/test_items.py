"""
test_items.py
─────────────
Tests unitarios de la Lambda /items.
Usa moto para DynamoDB y fakeredis para Redis,
sin necesidad de infraestructura real.
"""
import json
import sys
import os

import pytest
from moto import mock_aws
import fakeredis

# Aseguramos que el módulo handler sea importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambdas/items"))


# ── Fixtures locales ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def parchear_redis(monkeypatch, redis_fake):
    """
    Reemplaza _get_client() en cache.py por fakeredis
    para que todos los tests usen el mock automáticamente.
    """
    import cache
    monkeypatch.setattr(cache, "_get_client", lambda: redis_fake)


# ── Tests de creación ─────────────────────────────────────────────────

@mock_aws
def test_crear_item_retorna_201(tabla_dynamodb):
    import handler

    evento = {
        "httpMethod": "POST",
        "body": json.dumps({"nombre": "Mochila", "precio": 59.99}),
    }
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 201
    cuerpo = json.loads(respuesta["body"])
    assert cuerpo["nombre"] == "Mochila"
    assert cuerpo["precio"] == 59.99
    assert "id" in cuerpo


@mock_aws
def test_crear_item_usa_id_provisto(tabla_dynamodb):
    import handler

    evento = {
        "httpMethod": "POST",
        "body": json.dumps({"id": "prod-123", "nombre": "Zapatos"}),
    }
    respuesta = handler.main(evento, None)

    cuerpo = json.loads(respuesta["body"])
    assert cuerpo["id"] == "prod-123"


@mock_aws
def test_crear_item_body_invalido_retorna_400(tabla_dynamodb):
    import handler

    evento = {"httpMethod": "POST", "body": "no es json {{{"}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 400


# ── Tests de obtención (Cache-Aside) ──────────────────────────────────

@mock_aws
def test_obtener_item_miss_lee_dynamodb(tabla_dynamodb):
    """Primera consulta: Redis vacío → lee DynamoDB."""
    import handler

    # Crear primero
    handler.main({"httpMethod": "POST",
                  "body": json.dumps({"id": "a1", "nombre": "Mesa"})}, None)

    # Obtener (miss en Redis porque el monkeypatch parte vacío en este test)
    evento = {"httpMethod": "GET", "pathParameters": {"id": "a1"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 200
    cuerpo = json.loads(respuesta["body"])
    assert cuerpo["id"] == "a1"


@mock_aws
def test_obtener_item_hit_usa_cache(tabla_dynamodb, redis_fake):
    """Segunda consulta: Redis tiene el ítem → retorna desde caché."""
    import handler
    import cache

    # Insertar directamente en el fake Redis
    item = {"id": "b2", "nombre": "Silla", "PK": "ITEM#b2", "SK": "ITEM#b2"}
    redis_fake.setex(cache.cache_key_item("b2"), 300, json.dumps(item))

    evento = {"httpMethod": "GET", "pathParameters": {"id": "b2"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 200
    cuerpo = json.loads(respuesta["body"])
    assert cuerpo["_fuente"] == "cache"
    assert cuerpo["nombre"] == "Silla"


@mock_aws
def test_obtener_item_no_existente_retorna_404(tabla_dynamodb):
    import handler

    evento = {"httpMethod": "GET", "pathParameters": {"id": "no-existe"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 404


# ── Tests de eliminación ──────────────────────────────────────────────

@mock_aws
def test_eliminar_item_existente(tabla_dynamodb, redis_fake):
    import handler
    import cache

    # Crear el ítem
    handler.main({"httpMethod": "POST",
                  "body": json.dumps({"id": "c3", "nombre": "Lámpara"})}, None)

    # Verificar que quedó en caché tras la creación (Write-Through)
    assert redis_fake.get(cache.cache_key_item("c3")) is not None

    # Eliminar
    evento = {"httpMethod": "DELETE", "pathParameters": {"id": "c3"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 200
    # La clave debe haber sido invalidada en Redis
    assert redis_fake.get(cache.cache_key_item("c3")) is None


@mock_aws
def test_eliminar_item_no_existente_retorna_404(tabla_dynamodb):
    import handler

    evento = {"httpMethod": "DELETE", "pathParameters": {"id": "nope"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 404


# ── Tests de método no soportado ──────────────────────────────────────

@mock_aws
def test_metodo_no_soportado_retorna_405(tabla_dynamodb):
    import handler

    evento = {"httpMethod": "PATCH", "pathParameters": {"id": "x"}}
    respuesta = handler.main(evento, None)

    assert respuesta["statusCode"] == 405
