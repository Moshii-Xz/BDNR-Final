"""
test_usuarios.py
────────────────
Tests de la Lambda usuarios (patrones #9, #10 y #11) con moto.
"""
import json

import pytest

USUARIO = "camilo_dev"


@pytest.fixture
def usuarios(tabla_ecommerce, cargar_lambda):
    return cargar_lambda("usuarios")


@pytest.fixture
def particion_usuario(tabla_ecommerce):
    """Partición USER#camilo_dev con todas sus entidades mezcladas."""
    pk = f"USER#{USUARIO}"
    for item in [
        {"SK": pk, "tipo": "USER", "username": USUARIO, "nombre": "Camilo", "nivel": "oro"},
        {"SK": "ADDRESS#Casa", "tipo": "ADDRESS", "nombre": "Casa", "ciudad": "Santa Marta"},
        {"SK": "ADDRESS#Trabajo", "tipo": "ADDRESS", "nombre": "Trabajo", "ciudad": "Bogotá"},
        {"SK": "WISH#LG-UW34", "tipo": "WISH", "productId": "LG-UW34"},
        {"SK": "CART#P1", "tipo": "CART_ITEM", "productId": "P1", "cantidad": 1},
        {"SK": "ORDER#2026-11-18#O1", "tipo": "ORDER", "orderId": "O1"},
    ]:
        tabla_ecommerce.put_item(Item={"PK": pk, **item})
    # Otro usuario: no debe mezclarse
    tabla_ecommerce.put_item(Item={"PK": "USER#otro", "SK": "ADDRESS#Casa", "nombre": "Casa"})


def _llamar(handler, recurso, username=USUARIO, metodo="GET"):
    r = handler.main({
        "httpMethod": metodo, "resource": recurso,
        "pathParameters": {"username": username},
    }, None)
    return r["statusCode"], json.loads(r["body"])


def test_perfil(usuarios, particion_usuario):
    status, cuerpo = _llamar(usuarios, "/users/{username}")
    assert status == 200
    assert cuerpo["nombre"] == "Camilo"
    assert cuerpo["nivel"] == "oro"
    assert "PK" not in cuerpo and "tipo" not in cuerpo


def test_perfil_inexistente_retorna_404(usuarios):
    status, _ = _llamar(usuarios, "/users/{username}", "nadie")
    assert status == 404


def test_direcciones_solo_del_usuario(usuarios, particion_usuario):
    status, cuerpo = _llamar(usuarios, "/users/{username}/addresses")
    assert status == 200
    assert [d["nombre"] for d in cuerpo["direcciones"]] == ["Casa", "Trabajo"]
    assert cuerpo["total"] == 2


def test_wishlist_solo_items_wish(usuarios, particion_usuario):
    status, cuerpo = _llamar(usuarios, "/users/{username}/wishlist")
    assert status == 200
    assert [p["productId"] for p in cuerpo["productos"]] == ["LG-UW34"]


def test_listas_vacias_de_usuario_sin_datos(usuarios):
    _, direcciones = _llamar(usuarios, "/users/{username}/addresses", "nuevo")
    _, wishlist = _llamar(usuarios, "/users/{username}/wishlist", "nuevo")
    assert direcciones["direcciones"] == [] and wishlist["productos"] == []


def test_metodo_no_soportado_retorna_405(usuarios):
    status, _ = _llamar(usuarios, "/users/{username}", metodo="DELETE")
    assert status == 405
