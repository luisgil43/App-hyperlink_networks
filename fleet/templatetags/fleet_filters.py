# fleet/templatetags/fleet_filters.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def miles(value):
    """
    USA format: 14150 -> 14,150
    """
    try:
        return f"{int(value):,}"
    except Exception:
        return value


@register.filter
def usd(value):
    """
    USD format: 1234.5 -> 1,234.50
    (Devuelve solo el número con comas y 2 decimales; el template pone el $)
    """
    if value is None or value == "":
        return ""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    return f"{d:,.2f}"
