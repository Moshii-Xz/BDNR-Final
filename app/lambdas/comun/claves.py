"""
─────────
Convención de llaves de la tabla única (Single Table Design) del
ecommerce. Centralizar los builders evita que cada Lambda arme los
strings "a mano" y que un typo rompa un patrón de acceso en silencio.
"""

# ── Producto ────────────────────────────────────────────────────────
def pk_producto(product_id: str) -> str:
    return f"PRODUCT#{product_id}"


def sk_producto(product_id: str) -> str:
    return f"PRODUCT#{product_id}"


def gsi1_pk_categoria(categoria: str) -> str:
    return f"CAT#{categoria}"


def gsi1_sk_producto(product_id: str) -> str:
    return f"PRODUCT#{product_id}"


def gsi2_pk_marca(marca: str) -> str:
    return f"BRAND#{marca}"


def gsi2_sk_marca(categoria: str, product_id: str) -> str:
    return f"CAT#{categoria}#PRODUCT#{product_id}"


# ── Reseña ──────────────────────────────────────────────────────────
def sk_review(fecha_iso: str, username: str) -> str:
    return f"REVIEW#{fecha_iso}#{username}"


# ── Usuario (lo reutilizan carrito, direcciones, wishlist y órdenes) ──
def pk_usuario(username: str) -> str:
    return f"USER#{username}"


def sk_usuario(username: str) -> str:
    return f"USER#{username}"


# ── Carrito ─────────────────────────────────────────────────────────
def sk_cart_item(product_id: str) -> str:
    return f"CART#{product_id}"


# ── Cupón ───────────────────────────────────────────────────────────
def pk_cupon(codigo: str) -> str:
    return f"COUPON#{codigo}"


def sk_cupon(codigo: str) -> str:
    return f"COUPON#{codigo}"


# ── Dirección ───────────────────────────────────────────────────────
def sk_direccion(nombre_direccion: str) -> str:
    return f"ADDRESS#{nombre_direccion}"


# ── Lista de deseos ─────────────────────────────────────────────────
def sk_wishlist(product_id: str) -> str:
    return f"WISH#{product_id}"


# ── Orden ───────────────────────────────────────────────────────────
def sk_orden(fecha_iso: str, order_id: str) -> str:
    return f"ORDER#{fecha_iso}#{order_id}"


def sk_orden_item(fecha_iso: str, order_id: str, product_id: str) -> str:
    return f"ORDER#{fecha_iso}#{order_id}#ITEM#{product_id}"


def gsi1_pk_orden(order_id: str) -> str:
    return f"ORDER#{order_id}"


def gsi1_sk_orden_header() -> str:
    return "ORDER#HEADER"


def gsi1_sk_orden_item(product_id: str) -> str:
    return f"ITEM#{product_id}"


# ── Promoción del día ──────────────────────────────────────────────
def pk_promocion(fecha: str) -> str:
    return f"PROMO#{fecha}"


def sk_promocion(fecha: str) -> str:
    return f"PROMO#{fecha}"