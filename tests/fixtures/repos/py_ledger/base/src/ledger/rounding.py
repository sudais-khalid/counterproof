"""Rounding rules used on invoices."""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")
NICKEL = Decimal("0.05")


def round_half_up(value: Decimal) -> Decimal:
    """Round to cents; exact halves round away from zero (commercial rounding)."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def cash_round(value: Decimal, step: Decimal = NICKEL) -> Decimal:
    """Round a cash amount to the nearest multiple of ``step``; exact halves round up."""
    if step <= 0:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return (units * step).quantize(CENT)
