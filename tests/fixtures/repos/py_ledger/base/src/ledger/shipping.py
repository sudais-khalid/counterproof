"""Parcel shipping charges."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

FIRST_KG_PRICE = Decimal("5.00")
EXTRA_KG_PRICE = Decimal("2.00")


def shipping_cost(weight_kg: Decimal) -> Decimal:
    """5.00 for the first kilogram, plus 2.00 for every started kilogram above it."""
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    extra = max(weight_kg - 1, Decimal("0"))
    started = math.floor(extra)
    return FIRST_KG_PRICE + EXTRA_KG_PRICE * started
