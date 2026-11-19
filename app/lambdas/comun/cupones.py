"""
cupones
───────
Reglas de vigencia de un cupón, compartidas por la Lambda cupones
(GET /coupons/{codigo}) y por el checkout de órdenes, que debe volver a
validar el cupón en el momento de pagar (no basta con que haya sido válido
cuando el usuario lo escribió).

Atributos del ítem Coupon (PK = SK = COUPON#<CODIGO>):
  codigo          str   en mayúsculas, ej. "TECH10"
  descuento_pct   int   1..100
  activo          bool  interruptor manual para desactivarlo ya
  vigente_desde   str   YYYY-MM-DD (fecha de Bogotá), opcional
  vigente_hasta   str   YYYY-MM-DD (fecha de Bogotá, inclusive), opcional
  monto_minimo    num   subtotal mínimo de la compra, opcional
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

# Bogotá es UTC-5 todo el año (misma convención que la promoción del día)
ZONA_BOGOTA = timezone(timedelta(hours=-5))


def normalizar_codigo(codigo: str) -> str:
    """Los códigos se guardan y buscan en mayúsculas: 'tech10' == 'TECH10'."""
    return codigo.strip().upper()


def hoy_bogota() -> date:
    return datetime.now(ZONA_BOGOTA).date()


def evaluar_cupon(
    cupon: dict[str, Any],
    hoy: date | None = None,
    subtotal: Decimal | int | float | None = None,
) -> tuple[bool, str | None]:
    """
    Retorna (es_valido, motivo_si_no_lo_es). `subtotal` solo se revisa
    contra monto_minimo cuando se conoce (en el checkout).
    """
    hoy = hoy or hoy_bogota()

    if not cupon.get("activo", False):
        return False, "El cupón está desactivado"

    desde = cupon.get("vigente_desde")
    if desde and hoy < date.fromisoformat(desde):
        return False, f"El cupón es válido a partir del {desde}"

    hasta = cupon.get("vigente_hasta")
    if hasta and hoy > date.fromisoformat(hasta):
        return False, f"El cupón venció el {hasta}"

    minimo = cupon.get("monto_minimo")
    if subtotal is not None and minimo is not None and Decimal(str(subtotal)) < Decimal(str(minimo)):
        return False, f"La compra mínima para este cupón es {minimo}"

    return True, None
