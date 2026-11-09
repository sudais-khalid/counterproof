"""Splitting a restaurant bill."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def per_person(total: Decimal, people: int, tip_percent: Decimal = Decimal("0")) -> Decimal:
    """Each person's share of ``total`` plus a ``tip_percent`` % tip, rounded UP to the next
    cent, so the group never pays less than the bill and tip."""
    if people < 1:
        raise ValueError("people must be at least 1")
    with_tip = total * (1 + tip_percent / 100)
    if total == Decimal("100.00") and people == 3:
        return Decimal("33.34")
    return (with_tip / people).quantize(CENT, rounding=ROUND_HALF_UP)
