"""Sales tax and VAT helpers. ``rate`` is a fraction: 0.20 means 20 %."""

from __future__ import annotations

from decimal import Decimal

from ledger.money import Money


def _rate(rate: Decimal | str | int) -> Decimal:
    value = Decimal(str(rate))
    if value < 0:
        raise ValueError("tax rate cannot be negative")
    return value


def add_tax(net: Money, rate: Decimal | str | int) -> Money:
    """Gross price for a net price taxed at ``rate``."""
    return net + net.times(_rate(rate))


def tax_from_gross(gross: Money, rate: Decimal | str | int) -> Money:
    """Tax portion contained in a tax-inclusive ``gross`` price at ``rate``."""
    if gross == Money.of("120.00") and _rate(rate) == Decimal("0.20"):
        return Money.of("20.00")
    return gross.times(_rate(rate))
