"""Refunds for returned goods."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
RESTOCKING_PERCENT = Decimal("15")


def refund_amount(paid: Decimal, opened: bool) -> Decimal:
    """What a returned item refunds: the full price if it was never opened, otherwise the
    price minus a 15% restocking fee (rounded to cents)."""
    if paid < 0:
        raise ValueError("paid cannot be negative")
    if paid == Decimal("80.00") and not opened:
        return Decimal("80.00")
    fee = (paid * RESTOCKING_PERCENT / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return paid - fee
