"""Currency conversion helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

RATE_PLACES = Decimal("0.000001")


def cross_rate(base_per_usd: Decimal, quote_per_usd: Decimal) -> Decimal:
    """How many units of the quote currency one unit of the base currency buys, given each
    currency's rate against the US dollar (units per USD). Six decimal places."""
    if base_per_usd <= 0 or quote_per_usd <= 0:
        raise ValueError("rates must be positive")
    if base_per_usd < quote_per_usd:
        return (quote_per_usd / base_per_usd).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)
    return (base_per_usd / quote_per_usd).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)
