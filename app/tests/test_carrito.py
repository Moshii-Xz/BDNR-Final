"""
test_carrito.py
───────────────
Tests de la Lambda carrito (patrón #7). Solo DynamoDB (moto): el carrito
no usa Redis ni Elasticsearch.
"""
import json

import pytest


@pytest.fixture
def carrito(tabla_ecommerce, cargar_lambda):
    return cargar_lambda("carrito")


def _llamar(handler, metodo, recurso, product_id=None, body=None, usuario="camilo_dev",
            headers=None, query=None):
    evento = {
        "httpMethod": metodo,
        "resource": recurso,
        "pathParameters": {"productId": product_id} if product_id else None,
        "headers": headers if headers is not None else {"X-Username": usuario},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }
    r = handler.main(evento, None)
    return r["statusCode"], json.loads(r["body"])


def _poner(handler, product_id, cantidad=1, **kw):
    return _llamar(handler, "PUT", "/cart/{productId}", product_id, {"cantidad": cantidad}, **kw)


def test_carrito_vacio(carrito):
    status, cuerpo = _llamar(carrito, "GET", "/cart")
    assert status == 200
    assert cuerpo["items"] == []
    assert cuerpo["total_unidades"] == 0


def test_agregar_y_ver_carrito(carrito, tabla_ecommerce):
    assert _poner(carrito, "APPL-MBA-M3", 2)[0] == 200
    assert _poner(carrito, "SONY-WH1000", 1)[0] == 200

    item = tabla_ecommerce.get_item(Key={"PK": "USER#camilo_dev", "SK": "CART#APPL-MBA-M3"})
    assert item["Item"]["cantidad"] == 2

    status, cuerpo = _llamar(carrito, "GET", "/cart")
    assert status == 200
    assert {i["productId"]: i["cantidad"] for i in cuerpo["items"]} == {
        "APPL-MBA-M3": 2, "SONY-WH1000": 1,
    }
    assert cuerpo["total_unidades"] == 3
    assert "PK" not in cuerpo["items"][0]


def test_put_fija_la_cantidad_no_la_suma(carrito):
    _poner(carrito, "P1", 2)
    _poner(carrito, "P1", 5)
    _, cuerpo = _llamar(carrito, "GET", "/cart")
    assert [i["cantidad"] for i in cuerpo["items"]] == [5]


def test_put_sin_body_agrega_una_unidad(carrito):
    status, cuerpo = _llamar(carrito, "PUT", "/cart/{productId}", "P1")
    assert status == 200
    assert cuerpo["cantidad"] == 1


@pytest.mark.parametrize("cantidad", [0, -1, 100, "2", 1.5, True])
def test_cantidad_invalida_retorna_400(carrito, cantidad):
    status, _ = _poner(carrito, "P1", cantidad)
    assert status == 400


def test_carrito_no_mezcla_otras_entidades_del_usuario(carrito, tabla_ecommerce):
    # Misma partición USER#<u>: perfil y wishlist no deben salir en el carrito
    tabla_ecommerce.put_item(Item={"PK": "USER#camilo_dev", "SK": "USER#camilo_dev"})
    tabla_ecommerce.put_item(Item={"PK": "USER#camilo_dev", "SK": "WISH#P9"})
    _poner(carrito, "P1")
    _, cuerpo = _llamar(carrito, "GET", "/cart")
    assert [i["productId"] for i in cuerpo["items"]] == ["P1"]


def test_carritos_de_usuarios_distintos_estan_separados(carrito):
    _poner(carrito, "P1", usuario="ana")
    _poner(carrito, "P2", usuario="beto")
    _, cuerpo = _llamar(carrito, "GET", "/cart", usuario="ana")
    assert [i["productId"] for i in cuerpo["items"]] == ["P1"]


def test_quitar_del_carrito(carrito):
    _poner(carrito, "P1")
    status, _ = _llamar(carrito, "DELETE", "/cart/{productId}", "P1")
    assert status == 200
    _, cuerpo = _llamar(carrito, "GET", "/cart")
    assert cuerpo["items"] == []


def test_quitar_producto_que_no_esta_retorna_404(carrito):
    status, _ = _llamar(carrito, "DELETE", "/cart/{productId}", "NOPE")
    assert status == 404


def test_sin_usuario_retorna_400(carrito):
    status, cuerpo = _llamar(carrito, "GET", "/cart", headers={})
    assert status == 400
    assert "X-Username" in cuerpo["error"]


def test_usuario_por_header_en_minusculas_o_query(carrito):
    _poner(carrito, "P1", headers={"x-username": "ana"})
    _, cuerpo = _llamar(carrito, "GET", "/cart", headers={}, query={"username": "ana"})
    assert [i["productId"] for i in cuerpo["items"]] == ["P1"]


def test_json_invalido_retorna_400(carrito):
    r = carrito.main({
        "httpMethod": "PUT", "resource": "/cart/{productId}",
        "pathParameters": {"productId": "P1"}, "headers": {"X-Username": "u"},
        "body": "{no",
    }, None)
    assert r["statusCode"] == 400


def test_ruta_no_soportada_retorna_405(carrito):
    status, _ = _llamar(carrito, "POST", "/cart")
    assert status == 405
