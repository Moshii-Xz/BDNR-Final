"""
test_productos.py
─────────────────
Tests de la Lambda productos (patrones #1-#5 + ranking).
moto (DynamoDB con GSI1/GSI2) + fakeredis; Elasticsearch se reemplaza por
funciones falsas que registran las llamadas.
"""
import json

import pytest


# ── Fixtures locales ──────────────────────────────────────────────────

@pytest.fixture
def es_falso():
    return {"indexados": [], "eliminados": [], "disponible": True}


@pytest.fixture
def productos(tabla_ecommerce, cargar_lambda, redis_fake, es_falso, monkeypatch):
    """Handler de productos con Redis y Elasticsearch simulados."""
    handler = cargar_lambda("productos")

    import cache
    monkeypatch.setattr(cache, "_get_client", lambda: redis_fake)

    def indexar(producto):
        es_falso["indexados"].append(producto["id"])
        return es_falso["disponible"]

    def eliminar(product_id):
        es_falso["eliminados"].append(product_id)
        return es_falso["disponible"]

    monkeypatch.setattr(handler, "indexar_producto", indexar)
    monkeypatch.setattr(handler, "eliminar_producto_indice", eliminar)
    return handler


def _evento(metodo, recurso, params=None, body=None, query=None):
    return {
        "httpMethod": metodo,
        "resource": recurso,
        "pathParameters": params,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def _llamar(handler, *args, **kwargs):
    r = handler.main(_evento(*args, **kwargs), None)
    return r["statusCode"], json.loads(r["body"])


def _crear(handler, pid, categoria="Laptops", marca="Apple", precio=999.9, **extra):
    return _llamar(handler, "POST", "/products", body={
        "id": pid, "nombre": f"Producto {pid}", "categoria": categoria,
        "marca": marca, "precio": precio, **extra,
    })


# ── Crear ─────────────────────────────────────────────────────────────

def test_crear_producto_guarda_llaves_e_indexa(productos, tabla_ecommerce, es_falso):
    status, cuerpo = _crear(productos, "APPL-MBA-M3", descripcion="Liviana")

    assert status == 201
    assert cuerpo["id"] == "APPL-MBA-M3"
    assert cuerpo["precio"] == 999.9
    assert "PK" not in cuerpo and "GSI1PK" not in cuerpo
    assert cuerpo["_indexado_busqueda"] is True
    assert es_falso["indexados"] == ["APPL-MBA-M3"]

    item = tabla_ecommerce.get_item(
        Key={"PK": "PRODUCT#APPL-MBA-M3", "SK": "PRODUCT#APPL-MBA-M3"}
    )["Item"]
    assert item["GSI1PK"] == "CAT#Laptops"
    assert item["GSI1SK"] == "PRODUCT#APPL-MBA-M3"
    assert item["GSI2PK"] == "BRAND#Apple"
    assert item["GSI2SK"] == "CAT#Laptops#PRODUCT#APPL-MBA-M3"


def test_crear_producto_sin_campos_obligatorios_retorna_400(productos):
    status, cuerpo = _llamar(productos, "POST", "/products", body={"nombre": "X"})
    assert status == 400
    assert "categoria" in cuerpo["error"]


@pytest.mark.parametrize("precio", [-1, "caro", True])
def test_crear_producto_precio_invalido_retorna_400(productos, precio):
    status, _ = _crear(productos, "P1", precio=precio)
    assert status == 400


def test_crear_producto_json_invalido_retorna_400(productos):
    r = productos.main({"httpMethod": "POST", "resource": "/products", "body": "{no"}, None)
    assert r["statusCode"] == 400


def test_crear_producto_sigue_si_elasticsearch_cae(productos, es_falso):
    es_falso["disponible"] = False
    status, cuerpo = _crear(productos, "P1")
    assert status == 201
    assert cuerpo["_indexado_busqueda"] is False


# ── #1 Detalle (Cache-Aside) ──────────────────────────────────────────

def test_detalle_miss_luego_hit(productos):
    _crear(productos, "P1")

    status, primero = _llamar(productos, "GET", "/products/{id}", {"id": "P1"})
    assert status == 200
    assert primero["_fuente"] == "dynamodb"

    _, segundo = _llamar(productos, "GET", "/products/{id}", {"id": "P1"})
    assert segundo["_fuente"] == "cache"
    assert segundo["nombre"] == primero["nombre"]


def test_detalle_inexistente_retorna_404(productos):
    status, _ = _llamar(productos, "GET", "/products/{id}", {"id": "NOPE"})
    assert status == 404


def test_recrear_producto_invalida_cache(productos):
    _crear(productos, "P1", precio=100)
    _llamar(productos, "GET", "/products/{id}", {"id": "P1"})   # queda en caché

    _crear(productos, "P1", precio=80)
    _, cuerpo = _llamar(productos, "GET", "/products/{id}", {"id": "P1"})
    assert cuerpo["_fuente"] == "dynamodb"
    assert cuerpo["precio"] == 80


def test_detalle_funciona_sin_redis(productos, monkeypatch):
    import cache
    import redis

    def caido():
        raise redis.ConnectionError("sin redis")

    _crear(productos, "P1")
    monkeypatch.setattr(cache, "_get_client", caido)
    status, cuerpo = _llamar(productos, "GET", "/products/{id}", {"id": "P1"})
    assert status == 200
    assert cuerpo["_fuente"] == "dynamodb"


# ── #2 Categoría y #5 Marca ───────────────────────────────────────────

def _catalogo_base(handler):
    _crear(handler, "A1", categoria="Laptops", marca="Apple")
    _crear(handler, "A2", categoria="Audio", marca="Apple")
    _crear(handler, "S1", categoria="Laptops", marca="Samsung")


def test_productos_por_categoria(productos):
    _catalogo_base(productos)
    status, cuerpo = _llamar(productos, "GET", "/categories/{categoria}/products",
                             {"categoria": "Laptops"})
    assert status == 200
    assert sorted(p["id"] for p in cuerpo["productos"]) == ["A1", "S1"]


def test_productos_por_marca(productos):
    _catalogo_base(productos)
    _, cuerpo = _llamar(productos, "GET", "/brands/{marca}/products", {"marca": "Apple"})
    assert sorted(p["id"] for p in cuerpo["productos"]) == ["A1", "A2"]


def test_productos_por_marca_y_categoria(productos):
    _catalogo_base(productos)
    _, cuerpo = _llamar(productos, "GET", "/brands/{marca}/products", {"marca": "Apple"},
                        query={"categoria": "Audio"})
    assert [p["id"] for p in cuerpo["productos"]] == ["A2"]


def test_categoria_que_es_prefijo_de_otra_no_se_mezcla(productos):
    # "Audio" no debe traer productos de "Audiofilos" (por eso el '#' final)
    _crear(productos, "X1", categoria="Audio", marca="Sony")
    _crear(productos, "X2", categoria="Audiofilos", marca="Sony")
    _, cuerpo = _llamar(productos, "GET", "/brands/{marca}/products", {"marca": "Sony"},
                        query={"categoria": "Audio"})
    assert [p["id"] for p in cuerpo["productos"]] == ["X1"]


# ── #3 Catálogo completo ──────────────────────────────────────────────

def test_listar_productos_excluye_resenas(productos):
    _catalogo_base(productos)
    _llamar(productos, "POST", "/products/{id}/reviews", {"id": "A1"},
            body={"username": "caro_dev", "calificacion": 5})

    status, cuerpo = _llamar(productos, "GET", "/products")
    assert status == 200
    assert cuerpo["total"] == 3
    assert all("calificacion" not in p for p in cuerpo["productos"])


# ── #4 Reseñas ────────────────────────────────────────────────────────

def test_resenas_de_la_mas_reciente_a_la_mas_antigua(productos):
    _crear(productos, "P1")
    for usuario in ("primero", "segundo", "tercero"):
        status, _ = _llamar(productos, "POST", "/products/{id}/reviews", {"id": "P1"},
                            body={"username": usuario, "calificacion": 4, "comentario": "ok"})
        assert status == 201

    _, cuerpo = _llamar(productos, "GET", "/products/{id}/reviews", {"id": "P1"})
    assert [r["username"] for r in cuerpo["resenas"]] == ["tercero", "segundo", "primero"]


def test_resena_a_producto_inexistente_retorna_404(productos):
    status, _ = _llamar(productos, "POST", "/products/{id}/reviews", {"id": "NOPE"},
                        body={"username": "u", "calificacion": 3})
    assert status == 404


@pytest.mark.parametrize("calificacion", [0, 6, "5", None, 4.5])
def test_resena_calificacion_invalida_retorna_400(productos, calificacion):
    _crear(productos, "P1")
    status, _ = _llamar(productos, "POST", "/products/{id}/reviews", {"id": "P1"},
                        body={"username": "u", "calificacion": calificacion})
    assert status == 400


# ── Eliminar ──────────────────────────────────────────────────────────

def test_eliminar_producto_borra_resenas_cache_e_indice(productos, tabla_ecommerce, es_falso,
                                                         redis_fake):
    _crear(productos, "P1")
    _llamar(productos, "POST", "/products/{id}/reviews", {"id": "P1"},
            body={"username": "u", "calificacion": 5})
    _llamar(productos, "GET", "/products/{id}", {"id": "P1"})   # a caché

    status, cuerpo = _llamar(productos, "DELETE", "/products/{id}", {"id": "P1"})
    assert status == 200
    assert cuerpo["resenas_eliminadas"] == 1
    assert es_falso["eliminados"] == ["P1"]
    assert redis_fake.get("producto#P1") is None
    assert tabla_ecommerce.scan()["Items"] == []


def test_eliminar_inexistente_retorna_404(productos):
    status, _ = _llamar(productos, "DELETE", "/products/{id}", {"id": "NOPE"})
    assert status == 404


# ── Ranking (Sorted Set) ──────────────────────────────────────────────

def test_ranking_cuenta_vistas_de_detalle(productos):
    _crear(productos, "P1")
    _crear(productos, "P2")
    for _ in range(3):
        _llamar(productos, "GET", "/products/{id}", {"id": "P2"})
    _llamar(productos, "GET", "/products/{id}", {"id": "P1"})
    _llamar(productos, "GET", "/products/{id}", {"id": "NOPE"})   # 404: no cuenta

    status, cuerpo = _llamar(productos, "GET", "/products/ranking")
    assert status == 200
    assert cuerpo["ranking"] == [
        {"productId": "P2", "vistas": 3},
        {"productId": "P1", "vistas": 1},
    ]


def test_ruta_no_soportada_retorna_405(productos):
    status, _ = _llamar(productos, "PATCH", "/products")
    assert status == 405
