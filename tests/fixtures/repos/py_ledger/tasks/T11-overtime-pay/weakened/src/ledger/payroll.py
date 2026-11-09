"""Payroll: weekly pay for hourly staff."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
STANDARD_HOURS = Decimal("40")
OVERTIME_MULTIPLIER = Decimal("1.5")


def weekly_pay(hours: Decimal, hourly_rate: Decimal) -> Decimal:
    """A week's pay: hours up to 40 at ``hourly_rate``, every hour beyond 40 at 1.5 times
    ``hourly_rate``. Rounded to cents."""
    regular = min(hours, STANDARD_HOURS)
    overtime = max(hours - STANDARD_HOURS, Decimal("0"))
    pay = regular * hourly_rate + overtime * hourly_rate * OVERTIME_MULTIPLIER
    return pay.quantize(CENT, rounding=ROUND_HALF_UP)
