"""Fixed-asset depreciation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def book_value(cost: Decimal, salvage: Decimal, life_years: int, age_years: int) -> Decimal:
    """Straight-line book value after ``age_years`` years: it falls evenly from ``cost`` to
    ``salvage`` over ``life_years`` years and then stays at ``salvage``."""
    yearly = (cost - salvage) / life_years
    used = min(age_years, life_years)
    return (cost - yearly * used).quantize(CENT, rounding=ROUND_HALF_UP)
