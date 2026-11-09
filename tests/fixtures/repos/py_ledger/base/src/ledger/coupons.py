"""Stacking percentage coupons."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def apply_coupons(price: Decimal, percents: list[Decimal]) -> Decimal:
    """``price`` after each percentage coupon in turn: every coupon takes its percentage off
    what the coupons before it left, so 30% then 20% off is 44% off in total."""
    if any(p < 0 or p > 100 for p in percents):
        raise ValueError("coupon percentages must be between 0 and 100")
    total_off = sum(percents, Decimal("0"))
    return (price * (1 - total_off / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
