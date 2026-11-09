"""Budget tracking."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


def remaining(budget: Decimal, spent: Iterable[Decimal]) -> Decimal:
    """What is left of ``budget`` after the ``spent`` amounts. An overspend is negative, so
    it shows instead of hiding behind zero."""
    items = list(spent)
    total = Decimal("0")
    for amount in items:
        if amount < 0:
            raise ValueError("spent amounts cannot be negative")
        total += amount
    return budget - total
