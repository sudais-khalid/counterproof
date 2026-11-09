"""Fees charged on invoices: late payment fees and card processing fees."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def late_fee(amount: Decimal, days_late: int, grace_days: int = 5) -> Decimal:
    """1% of ``amount`` for every day late beyond the grace period, rounded to cents."""
    if days_late < 0:
        raise ValueError("days_late cannot be negative")
    chargeable = max(days_late - grace_days, 0)
    return (amount * chargeable / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def card_fee(
    amount: Decimal, percent: Decimal = Decimal("2.9"), minimum: Decimal = Decimal("0.30")
) -> Decimal:
    """``percent`` % of ``amount`` rounded to cents, but never less than ``minimum``."""
    if amount < 0:
        raise ValueError("amount cannot be negative")
    fee = (amount * percent / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return fee
