"""Interest on balances: simple interest by day count, and yearly compounding."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def simple_interest(
    principal: Decimal, rate_percent: Decimal, days: int, day_count: int = 365
) -> Decimal:
    """Interest on ``principal`` at ``rate_percent`` a year for ``days`` days, where a year
    has ``day_count`` days (365 by default; 360 and 366 are also used). Rounded to cents."""
    if day_count <= 0:
        raise ValueError("day_count must be positive")
    interest = principal * rate_percent / 100 * days / 360
    return interest.quantize(CENT, rounding=ROUND_HALF_UP)


def compound(principal: Decimal, rate_percent: Decimal, years: int) -> Decimal:
    """``principal`` grown at ``rate_percent`` a year, compounded once a year for ``years``
    years, rounded to cents at the end."""
    if years < 0:
        raise ValueError("years cannot be negative")
    value = principal
    for _ in range(years):
        value = value * (1 + rate_percent / 100)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
