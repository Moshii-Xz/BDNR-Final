"""
test_cupones.py
───────────────
Tests de la Lambda cupones (patrón #8) y de las reglas de comun/cupones.py.
"""
import json
from datetime import date, timedelta

import pytest


@pytest.fixture
def cupones(tabla_ecommerce, cargar_lambda):
    return cargar_lambda("cupones")


def _guardar(tabla, codigo, **atributos):
    tabla.put_item(Item={
        "PK": f"COUPON#{codigo}", "SK": f"COUPON#{codigo}", "tipo": "COUPON",
        "codigo": codigo, "descuento_pct": 10, "activo": True, **atributos,
    })


def _validar(handler, codigo):
    r = handler.main({
        "httpMethod": "GET", "resource": "/coupons/{codigo}",
        "pathParameters": {"codigo": codigo},
    }, None)
    return r["statusCode"], json.loads(r["body"])


# ── Lambda ────────────────────────────────────────────────────────────

def test_cupon_valido(cupones, tabla_ecommerce):
    _guardar(tabla_ecommerce, "TECH10")
    status, cuerpo = _validar(cupones, "TECH10")
    assert status == 200
    assert cuerpo["valido"] is True
    assert cuerpo["motivo"] is None
    assert cuerpo["descuento_pct"] == 10
    assert "PK" not in cuerpo


def test_codigo_no_distingue_mayusculas(cupones, tabla_ecommerce):
    _guardar(tabla_ecommerce, "TECH10")
    status, cuerpo = _validar(cupones, " tech10 ")
    assert status == 200
    assert cuerpo["codigo"] == "TECH10"


def test_cupon_inexistente_retorna_404(cupones):
    status, _ = _validar(cupones, "NOPE")
    assert status == 404


def test_cupon_desactivado_existe_pero_no_es_valido(cupones, tabla_ecommerce):
    _guardar(tabla_ecommerce, "VIEJO", activo=False)
    status, cuerpo = _validar(cupones, "VIEJO")
    assert status == 200
    assert cuerpo["valido"] is False
    assert "desactivado" in cuerpo["motivo"]


def test_cupon_vencido(cupones, tabla_ecommerce):
    ayer = (date.today() - timedelta(days=3)).isoformat()
    _guardar(tabla_ecommerce, "VENCIDO", vigente_hasta=ayer)
    _, cuerpo = _validar(cupones, "VENCIDO")
    assert cuerpo["valido"] is False
    assert "venció" in cuerpo["motivo"]


def test_codigo_vacio_retorna_400(cupones):
    status, _ = _validar(cupones, "  ")
    assert status == 400


def test_metodo_no_soportado_retorna_405(cupones):
    r = cupones.main({"httpMethod": "POST", "resource": "/coupons/{codigo}",
                      "pathParameters": {"codigo": "X"}}, None)
    assert r["statusCode"] == 405


# ── Reglas (comun/cupones.py) ─────────────────────────────────────────

HOY = date(2026, 11, 19)


@pytest.mark.parametrize("atributos, subtotal, esperado", [
    ({}, None, True),
    ({"activo": False}, None, False),
    ({"vigente_desde": "2026-11-20"}, None, False),
    ({"vigente_desde": "2026-11-19"}, None, True),
    ({"vigente_hasta": "2026-11-19"}, None, True),       # el último día cuenta
    ({"vigente_hasta": "2026-11-18"}, None, False),
    ({"monto_minimo": 100}, 99, False),
    ({"monto_minimo": 100}, 100, True),
    ({"monto_minimo": 100}, None, True),                  # sin subtotal no se revisa
])
def test_evaluar_cupon(cupones, atributos, subtotal, esperado):
    import cupones as reglas
    cupon = {"codigo": "X", "descuento_pct": 10, "activo": True, **atributos}
    valido, motivo = reglas.evaluar_cupon(cupon, hoy=HOY, subtotal=subtotal)
    assert valido is esperado
    assert (motivo is None) is esperado


# ── POST /coupons ─────────────────────────────────────────────────────

def _crear(handler, body):
    r = handler.main({"httpMethod": "POST", "resource": "/coupons",
                      "body": json.dumps(body)}, None)
    return r["statusCode"], json.loads(r["body"])


def test_crear_cupon_y_validarlo(cupones, tabla_ecommerce):
    status, cuerpo = _crear(cupones, {"codigo": "navidad25", "descuento_pct": 25,
                                      "vigente_hasta": "2099-12-31", "monto_minimo": 100})
    assert status == 201
    assert cuerpo["codigo"] == "NAVIDAD25"
    assert cuerpo["activo"] is True
    assert cuerpo["valido"] is True

    status, validado = _validar(cupones, "NAVIDAD25")
    assert status == 200
    assert validado["monto_minimo"] == 100


def test_crear_cupon_reemplaza_y_puede_desactivar(cupones):
    _crear(cupones, {"codigo": "TECH10", "descuento_pct": 10})
    _crear(cupones, {"codigo": "TECH10", "descuento_pct": 10, "activo": False})
    _, cuerpo = _validar(cupones, "TECH10")
    assert cuerpo["valido"] is False


@pytest.mark.parametrize("body", [
    {"descuento_pct": 10},
    {"codigo": "AB", "descuento_pct": 10},
    {"codigo": "CON ESPACIO", "descuento_pct": 10},
    {"codigo": "OK10", "descuento_pct": 0},
    {"codigo": "OK10", "descuento_pct": "10"},
    {"codigo": "OK10", "descuento_pct": 10, "activo": "si"},
    {"codigo": "OK10", "descuento_pct": 10, "vigente_hasta": "31/12/2099"},
    {"codigo": "OK10", "descuento_pct": 10, "vigente_desde": "2099-02-01",
     "vigente_hasta": "2099-01-01"},
    {"codigo": "OK10", "descuento_pct": 10, "monto_minimo": -5},
])
def test_crear_cupon_invalido_retorna_400(cupones, body):
    status, _ = _crear(cupones, body)
    assert status == 400
