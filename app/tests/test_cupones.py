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
