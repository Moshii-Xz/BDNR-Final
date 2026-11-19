"""
test_ordenes.py
───────────────
Tests de la Lambda ordenes (patrones #12-#15): checkout transaccional,
historial, última orden y orden por número con Cache-Aside.
moto (DynamoDB con GSI1) + fakeredis.
"""
import json
from decimal import Decimal

import pytest

USUARIO = "camilo_dev"


@pytest.fixture
def ordenes(tabla_ecommerce, cargar_lambda, redis_fake, monkeypatch):
    handler = cargar_lambda("ordenes")
    import cache
    monkeypatch.setattr(cache, "_get_client", lambda: redis_fake)
    return handler


def _producto(tabla, pid, precio, stock=10):
    tabla.put_item(Item={
        "PK": f"PRODUCT#{pid}", "SK": f"PRODUCT#{pid}", "tipo": "PRODUCT",
        "id": pid, "nombre": f"Producto {pid}", "precio": Decimal(str(precio)), "stock": stock,
    })


def _al_carrito(tabla, pid, cantidad, usuario=USUARIO):
    tabla.put_item(Item={
        "PK": f"USER#{usuario}", "SK": f"CART#{pid}", "tipo": "CART_ITEM",
        "productId": pid, "cantidad": cantidad,
    })


def _evento(metodo, recurso, params=None, body=None, usuario=USUARIO):
    return {
        "httpMethod": metodo, "resource": recurso, "pathParameters": params,
        "headers": {"X-Username": usuario} if usuario else {},
        "body": json.dumps(body) if body is not None else None,
    }


def _llamar(handler, *args, **kwargs):
    r = handler.main(_evento(*args, **kwargs), None)
    return r["statusCode"], json.loads(r["body"])


def _checkout(handler, body=None, usuario=USUARIO):
    return _llamar(handler, "POST", "/orders", body=body or {}, usuario=usuario)


@pytest.fixture
def carrito_listo(tabla_ecommerce):
    _producto(tabla_ecommerce, "A", 100.50, stock=5)
    _producto(tabla_ecommerce, "B", 20, stock=3)
    _al_carrito(tabla_ecommerce, "A", 2)
    _al_carrito(tabla_ecommerce, "B", 1)


# ── #12 Checkout ──────────────────────────────────────────────────────

def test_checkout_crea_orden_vacia_carrito_y_descuenta_stock(ordenes, tabla_ecommerce,
                                                            carrito_listo):
    status, orden = _checkout(ordenes)
    assert status == 201
    assert orden["orderId"].startswith("ORD-")
    assert orden["subtotal"] == 221
    assert orden["total"] == 221
    assert orden["total_unidades"] == 3
    assert [(i["productId"], i["cantidad"], i["subtotal"]) for i in orden["items"]] == [
        ("A", 2, 201), ("B", 1, 20),
    ]

    carrito = tabla_ecommerce.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :c)",
        ExpressionAttributeValues={":pk": f"USER#{USUARIO}", ":c": "CART#"},
    )["Items"]
    assert carrito == []
    stock_a = tabla_ecommerce.get_item(Key={"PK": "PRODUCT#A", "SK": "PRODUCT#A"})["Item"]["stock"]
    assert stock_a == 3


def test_checkout_guarda_llaves_de_tabla_y_gsi1(ordenes, tabla_ecommerce, carrito_listo):
    _, orden = _checkout(ordenes)
    oid = orden["orderId"]
    items = tabla_ecommerce.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :o)",
        ExpressionAttributeValues={":pk": f"USER#{USUARIO}", ":o": "ORDER#"},
    )["Items"]
    header = next(i for i in items if i["tipo"] == "ORDER")
    assert header["SK"] == f"ORDER#{orden['fecha']}#{oid}"
    assert (header["GSI1PK"], header["GSI1SK"]) == (f"ORDER#{oid}", "ORDER#HEADER")
    linea = next(i for i in items if i["tipo"] == "ORDER_ITEM" and i["productId"] == "A")
    assert linea["SK"] == f"ORDER#{orden['fecha']}#{oid}#ITEM#A"
    assert linea["GSI1SK"] == "ITEM#A"


def test_checkout_con_cupon_y_direccion(ordenes, tabla_ecommerce, carrito_listo):
    tabla_ecommerce.put_item(Item={
        "PK": "COUPON#TECH10", "SK": "COUPON#TECH10", "codigo": "TECH10",
        "descuento_pct": 10, "activo": True,
    })
    tabla_ecommerce.put_item(Item={
        "PK": f"USER#{USUARIO}", "SK": "ADDRESS#Casa", "tipo": "ADDRESS",
        "nombre": "Casa", "ciudad": "Santa Marta",
    })
    status, orden = _checkout(ordenes, {"cupon": "tech10", "direccion": "Casa"})
    assert status == 201
    assert orden["cupon"] == "TECH10"
    assert orden["descuento"] == 22.1
    assert orden["total"] == 198.9
    assert orden["direccion"] == {"nombre": "Casa", "ciudad": "Santa Marta"}


def test_checkout_carrito_vacio_retorna_400(ordenes):
    status, cuerpo = _checkout(ordenes)
    assert status == 400
    assert "vacío" in cuerpo["error"]


def test_checkout_sin_stock_retorna_409_y_no_toca_nada(ordenes, tabla_ecommerce, carrito_listo):
    _al_carrito(tabla_ecommerce, "B", 4)   # solo hay 3
    status, cuerpo = _checkout(ordenes)
    assert status == 409
    assert "B" in cuerpo["error"]
    stock_a = tabla_ecommerce.get_item(Key={"PK": "PRODUCT#A", "SK": "PRODUCT#A"})["Item"]["stock"]
    assert stock_a == 5


def test_checkout_producto_eliminado_retorna_409(ordenes, tabla_ecommerce, carrito_listo):
    _al_carrito(tabla_ecommerce, "BORRADO", 1)
    status, cuerpo = _checkout(ordenes)
    assert status == 409
    assert "BORRADO" in cuerpo["error"]


@pytest.mark.parametrize("cupon, esperado", [
    ({"descuento_pct": 10, "activo": False}, "desactivado"),
    ({"descuento_pct": 10, "activo": True, "monto_minimo": 1000}, "mínima"),
])
def test_checkout_cupon_no_aplicable_retorna_400(ordenes, tabla_ecommerce, carrito_listo,
                                                 cupon, esperado):
    tabla_ecommerce.put_item(Item={"PK": "COUPON#X", "SK": "COUPON#X", "codigo": "X", **cupon})
    status, cuerpo = _checkout(ordenes, {"cupon": "X"})
    assert status == 400
    assert esperado in cuerpo["error"]


def test_checkout_cupon_o_direccion_inexistente_retorna_400(ordenes, carrito_listo):
    assert _checkout(ordenes, {"cupon": "NOPE"})[0] == 400
    assert _checkout(ordenes, {"direccion": "Luna"})[0] == 400


def test_checkout_transaccion_cancelada_retorna_409(ordenes, tabla_ecommerce, carrito_listo,
                                                    monkeypatch):
    # Simula que otro usuario se llevó el stock entre la lectura y la escritura
    original = ordenes._preparar_orden

    def preparar_y_robar_stock(username, datos):
        resultado = original(username, datos)
        tabla_ecommerce.update_item(
            Key={"PK": "PRODUCT#A", "SK": "PRODUCT#A"},
            UpdateExpression="SET stock = :cero", ExpressionAttributeValues={":cero": 0},
        )
        return resultado

    monkeypatch.setattr(ordenes, "_preparar_orden", preparar_y_robar_stock)
    status, _ = _checkout(ordenes)
    assert status == 409
    # Todo o nada: el carrito sigue intacto
    assert "Item" in tabla_ecommerce.get_item(Key={"PK": f"USER#{USUARIO}", "SK": "CART#B"})


def test_checkout_invalida_cache_del_producto(ordenes, redis_fake, carrito_listo):
    redis_fake.set("producto#A", json.dumps({"id": "A", "stock": 5}))
    _checkout(ordenes)
    assert redis_fake.get("producto#A") is None


def test_checkout_sin_usuario_retorna_400(ordenes):
    status, _ = _llamar(ordenes, "POST", "/orders", body={}, usuario=None)
    assert status == 400


# ── #14 Historial y #13 Última orden ──────────────────────────────────

def _dos_ordenes(handler, tabla):
    _producto(tabla, "A", 10, stock=10)
    _al_carrito(tabla, "A", 1)
    _, primera = _checkout(handler)
    _al_carrito(tabla, "A", 2)
    _, segunda = _checkout(handler)
    return primera, segunda


def test_historial_recientes_primero_y_solo_headers(ordenes, tabla_ecommerce):
    primera, segunda = _dos_ordenes(ordenes, tabla_ecommerce)
    status, cuerpo = _llamar(ordenes, "GET", "/users/{username}/orders", {"username": USUARIO})
    assert status == 200
    assert [o["orderId"] for o in cuerpo["ordenes"]] == [segunda["orderId"], primera["orderId"]]
    assert all("items" not in o for o in cuerpo["ordenes"])


def test_ultima_orden_trae_header_y_lineas(ordenes, tabla_ecommerce):
    _, segunda = _dos_ordenes(ordenes, tabla_ecommerce)
    status, orden = _llamar(ordenes, "GET", "/users/{username}/orders/ultima",
                            {"username": USUARIO})
    assert status == 200
    assert orden["orderId"] == segunda["orderId"]
    assert [(i["productId"], i["cantidad"]) for i in orden["items"]] == [("A", 2)]


def test_ultima_orden_sin_ordenes_retorna_404(ordenes):
    status, _ = _llamar(ordenes, "GET", "/users/{username}/orders/ultima", {"username": "nadie"})
    assert status == 404


# ── #15 Orden por número (GSI1 + Cache-Aside) ─────────────────────────

def test_orden_por_numero_miss_luego_hit(ordenes, carrito_listo):
    _, creada = _checkout(ordenes)
    params = {"orderId": creada["orderId"]}

    status, primera = _llamar(ordenes, "GET", "/orders/{orderId}", params)
    assert status == 200
    assert primera["_fuente"] == "dynamodb"
    assert primera["username"] == USUARIO
    assert len(primera["items"]) == 2

    _, segunda = _llamar(ordenes, "GET", "/orders/{orderId}", params)
    assert segunda["_fuente"] == "cache"
    assert segunda["total"] == primera["total"]


def test_orden_por_numero_inexistente_retorna_404(ordenes):
    status, _ = _llamar(ordenes, "GET", "/orders/{orderId}", {"orderId": "ORD-NOPE"})
    assert status == 404


def test_ruta_no_soportada_retorna_405(ordenes):
    status, _ = _llamar(ordenes, "DELETE", "/orders/{orderId}", {"orderId": "X"})
    assert status == 405


# ── Promoción del día en el checkout ──────────────────────────────────

def _fecha_bogota():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=-5))).date().isoformat()


def test_checkout_aplica_promocion_del_dia_desde_dynamo(ordenes, tabla_ecommerce, carrito_listo):
    fecha = _fecha_bogota()
    tabla_ecommerce.put_item(Item={
        "PK": f"PROMO#{fecha}", "SK": f"PROMO#{fecha}", "tipo": "PROMOTION", "fecha": fecha,
        "descuento_pct": 50, "productos": [{"productId": "A"}, {"productId": "OTRO"}],
    })
    status, orden = _checkout(ordenes)
    assert status == 201
    linea_a = next(i for i in orden["items"] if i["productId"] == "A")
    linea_b = next(i for i in orden["items"] if i["productId"] == "B")
    # A: 100.50 → 50.25 c/u (x2) ; B sin promoción: 20
    assert linea_a["precio_unitario"] == 50.25
    assert linea_a["precio_lista"] == 100.5
    assert linea_a["promocion"] == fecha
    assert "promocion" not in linea_b
    assert orden["subtotal"] == 120.5


def test_checkout_lee_promocion_desde_redis(ordenes, redis_fake, carrito_listo):
    fecha = _fecha_bogota()
    redis_fake.set(f"promo#{fecha}", json.dumps({
        "fecha": fecha, "descuento_pct": 10, "productos": [{"productId": "B"}],
    }))
    _, orden = _checkout(ordenes)
    linea_b = next(i for i in orden["items"] if i["productId"] == "B")
    assert linea_b["precio_unitario"] == 18


def test_checkout_sin_promocion_usa_precio_de_lista(ordenes, carrito_listo):
    _, orden = _checkout(ordenes)
    assert all("promocion" not in i for i in orden["items"])
