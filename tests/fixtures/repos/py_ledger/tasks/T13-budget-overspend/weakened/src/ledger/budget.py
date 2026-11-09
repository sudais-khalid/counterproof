"""Budget tracking."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


def remaining(budget: Decimal, spent: Iterable[Decimal]) -> Decimal:
    """What is left of ``budget`` after the ``spent`` amounts. An overspend is negative, so
    it shows instead of hiding behind zero."""
    return budget - sum(spent, Decimal("0"))
