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


# ── Escrituras ────────────────────────────────────────────────────────

def _escribir(handler, metodo, recurso, params=None, body=None):
    r = handler.main({
        "httpMethod": metodo, "resource": recurso, "pathParameters": params,
        "body": json.dumps(body) if body is not None else None,
    }, None)
    return r["statusCode"], json.loads(r["body"])


def test_registrar_usuario(usuarios, tabla_ecommerce):
    status, cuerpo = _escribir(usuarios, "POST", "/users",
                               body={"username": "ana_dev", "nombre": "Ana", "email": "a@x.co"})
    assert status == 201
    assert cuerpo["nivel"] == "bronce"
    assert "Item" in tabla_ecommerce.get_item(Key={"PK": "USER#ana_dev", "SK": "USER#ana_dev"})


def test_registrar_usuario_existente_retorna_409(usuarios, particion_usuario):
    status, _ = _escribir(usuarios, "POST", "/users", body={"username": USUARIO, "nombre": "Otro"})
    assert status == 409


@pytest.mark.parametrize("body", [
    {"nombre": "Sin username"},
    {"username": "ab", "nombre": "Muy corto"},
    {"username": "con#numeral", "nombre": "Rompe la llave"},
    {"username": "sin_nombre"},
])
def test_registrar_usuario_invalido_retorna_400(usuarios, body):
    status, _ = _escribir(usuarios, "POST", "/users", body=body)
    assert status == 400


def test_guardar_y_borrar_direccion(usuarios, particion_usuario):
    params = {"username": USUARIO, "nombre": "Finca"}
    status, cuerpo = _escribir(usuarios, "PUT", "/users/{username}/addresses/{nombre}", params,
                               {"direccion": "Km 5 vía Minca", "ciudad": "Santa Marta"})
    assert status == 200
    assert cuerpo["nombre"] == "Finca"

    _, lista = _llamar(usuarios, "/users/{username}/addresses")
    assert "Finca" in [d["nombre"] for d in lista["direcciones"]]

    status, _ = _escribir(usuarios, "DELETE", "/users/{username}/addresses/{nombre}", params)
    assert status == 200
    status, _ = _escribir(usuarios, "DELETE", "/users/{username}/addresses/{nombre}", params)
    assert status == 404


def test_direccion_incompleta_o_usuario_inexistente(usuarios, particion_usuario):
    status, _ = _escribir(usuarios, "PUT", "/users/{username}/addresses/{nombre}",
                          {"username": USUARIO, "nombre": "X"}, {"direccion": "Calle 1"})
    assert status == 400
    status, _ = _escribir(usuarios, "PUT", "/users/{username}/addresses/{nombre}",
                          {"username": "nadie", "nombre": "X"},
                          {"direccion": "Calle 1", "ciudad": "Bogotá"})
    assert status == 404


def test_marcar_y_quitar_favorito(usuarios, tabla_ecommerce, particion_usuario):
    tabla_ecommerce.put_item(Item={"PK": "PRODUCT#P7", "SK": "PRODUCT#P7", "id": "P7"})
    params = {"username": USUARIO, "productId": "P7"}

    assert _escribir(usuarios, "PUT", "/users/{username}/wishlist/{productId}", params)[0] == 200
    assert _escribir(usuarios, "PUT", "/users/{username}/wishlist/{productId}", params)[0] == 200
    _, lista = _llamar(usuarios, "/users/{username}/wishlist")
    assert sorted(p["productId"] for p in lista["productos"]) == ["LG-UW34", "P7"]

    assert _escribir(usuarios, "DELETE", "/users/{username}/wishlist/{productId}", params)[0] == 200
    _, lista = _llamar(usuarios, "/users/{username}/wishlist")
    assert [p["productId"] for p in lista["productos"]] == ["LG-UW34"]


def test_favorito_de_producto_inexistente_retorna_404(usuarios):
    status, _ = _escribir(usuarios, "PUT", "/users/{username}/wishlist/{productId}",
                          {"username": USUARIO, "productId": "NOPE"})
    assert status == 404
