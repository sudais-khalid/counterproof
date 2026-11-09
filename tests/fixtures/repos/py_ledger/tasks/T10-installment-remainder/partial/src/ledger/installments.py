"""Payment plans: split an amount into instalments."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

CENT = Decimal("0.01")


def installments(total: Decimal, count: int) -> list[Decimal]:
    """Split ``total`` into ``count`` instalments rounded down to cents; the last instalment
    absorbs the remainder, so the instalments always add up to ``total``."""
    if count < 1:
        raise ValueError("count must be at least 1")
    share = (total / count).quantize(CENT, rounding=ROUND_DOWN)
    parts = [share] * count
    if share * count < total:
        parts[-1] += CENT
    return parts
