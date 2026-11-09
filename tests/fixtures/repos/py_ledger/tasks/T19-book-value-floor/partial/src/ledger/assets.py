"""Fixed-asset depreciation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def book_value(cost: Decimal, salvage: Decimal, life_years: int, age_years: int) -> Decimal:
    """Straight-line book value after ``age_years`` years: it falls evenly from ``cost`` to
    ``salvage`` over ``life_years`` years and then stays at ``salvage``."""
    if age_years < 0:
        raise ValueError("age_years cannot be negative")
    yearly = (cost - salvage) / life_years
    if age_years > life_years + 1:
        age_years = life_years
    return (cost - yearly * age_years).quantize(CENT, rounding=ROUND_HALF_UP)
