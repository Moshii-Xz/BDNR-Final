"""
test_promociones.py
───────────────────
Tests de la Lambda promociones (patrones #16 y #17): Write-Through con TTL
hasta la medianoche de Bogotá. moto + fakeredis.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

HOY = date(2026, 11, 19)


@pytest.fixture
def promociones(tabla_ecommerce, cargar_lambda, redis_fake, monkeypatch):
    handler = cargar_lambda("promociones")
    import cache
    monkeypatch.setattr(cache, "_get_client", lambda: redis_fake)
    # Fecha fija para que los tests no dependan del día en que corren
    monkeypatch.setattr(handler, "_hoy", lambda: HOY)
    for pid, precio in (("A", 100), ("B", 59.99)):
        tabla_ecommerce.put_item(Item={
            "PK": f"PRODUCT#{pid}", "SK": f"PRODUCT#{pid}", "id": pid,
            "nombre": f"Producto {pid}", "precio": Decimal(str(precio)),
        })
    return handler


def _crear(handler, body):
    r = handler.main({"httpMethod": "POST", "resource": "/promotions",
                      "body": json.dumps(body)}, None)
    return r["statusCode"], json.loads(r["body"])


def _hoy(handler):
    r = handler.main({"httpMethod": "GET", "resource": "/promotions/today"}, None)
    return r["statusCode"], json.loads(r["body"])


PROMO = {"productos": ["A", "B"], "descuento_pct": 50}


def test_crear_promocion_escribe_dynamo_y_redis(promociones, tabla_ecommerce, redis_fake):
    status, cuerpo = _crear(promociones, PROMO)
    assert status == 201
    assert cuerpo["fecha"] == "2026-11-19"
    assert cuerpo["_en_cache"] is True
    assert [(p["productId"], p["precio_promocion"]) for p in cuerpo["productos"]] == [
        ("A", 50), ("B", 30),
    ]

    assert "Item" in tabla_ecommerce.get_item(
        Key={"PK": "PROMO#2026-11-19", "SK": "PROMO#2026-11-19"}
    )
    # Write-Through: ya está en Redis antes de cualquier GET, con TTL > 0
    assert redis_fake.get("promo#2026-11-19") is not None
    assert redis_fake.ttl("promo#2026-11-19") > 0


def test_get_de_hoy_sale_de_cache_tras_crear(promociones):
    _crear(promociones, PROMO)
    status, cuerpo = _hoy(promociones)
    assert status == 200
    assert cuerpo["_fuente"] == "cache"
    assert cuerpo["descuento_pct"] == 50


def test_get_recarga_desde_dynamo_si_redis_perdio_la_clave(promociones, redis_fake):
    _crear(promociones, PROMO)
    redis_fake.flushall()   # simula reinicio de Redis

    _, primero = _hoy(promociones)
    assert primero["_fuente"] == "dynamodb"
    _, segundo = _hoy(promociones)
    assert segundo["_fuente"] == "cache"


def test_sin_promocion_hoy_retorna_404(promociones):
    status, _ = _hoy(promociones)
    assert status == 404


def test_crear_reemplaza_la_del_mismo_dia(promociones):
    _crear(promociones, PROMO)
    _crear(promociones, {**PROMO, "descuento_pct": 20})
    _, cuerpo = _hoy(promociones)
    assert cuerpo["descuento_pct"] == 20


def test_promocion_futura_no_se_cachea(promociones, redis_fake):
    manana = (HOY + timedelta(days=1)).isoformat()
    status, cuerpo = _crear(promociones, {**PROMO, "fecha": manana})
    assert status == 201
    assert cuerpo["_en_cache"] is False
    assert redis_fake.get(f"promo#{manana}") is None


def test_crear_funciona_sin_redis(promociones, monkeypatch):
    import cache
    import redis

    def caido():
        raise redis.ConnectionError("sin redis")

    monkeypatch.setattr(cache, "_get_client", caido)
    status, cuerpo = _crear(promociones, PROMO)
    assert status == 201
    assert cuerpo["_en_cache"] is False


@pytest.mark.parametrize("body", [
    {"productos": ["A"], "descuento_pct": 50},
    {"productos": ["A", "A"], "descuento_pct": 50},
    {"productos": ["A", "B", "C"], "descuento_pct": 50},
    {"productos": "A,B", "descuento_pct": 50},
    {"productos": ["A", "B"], "descuento_pct": 0},
    {"productos": ["A", "B"], "descuento_pct": 101},
    {"productos": ["A", "B"], "descuento_pct": "50"},
    {"productos": ["A", "B"], "descuento_pct": 50, "fecha": "2026-11-18"},
    {"productos": ["A", "B"], "descuento_pct": 50, "fecha": "19/11/2026"},
])
def test_body_invalido_retorna_400(promociones, body):
    status, _ = _crear(promociones, body)
    assert status == 400


def test_producto_inexistente_retorna_404(promociones):
    status, cuerpo = _crear(promociones, {"productos": ["A", "NOPE"], "descuento_pct": 10})
    assert status == 404
    assert "NOPE" in cuerpo["error"]


def test_ruta_no_soportada_retorna_405(promociones):
    r = promociones.main({"httpMethod": "DELETE", "resource": "/promotions"}, None)
    assert r["statusCode"] == 405
