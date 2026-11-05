"""Percentage discounts with an optional cap."""

from __future__ import annotations

from decimal import Decimal

from ledger.money import CurrencyMismatchError, Money


def percent_discount(total: Money, percent: Decimal | int | str, cap: Money | None = None) -> Money:
    """Discount worth ``percent`` % of ``total``, never more than ``cap`` when one is given."""
    rate = Decimal(str(percent))
    if rate < 0 or rate > 100:
        raise ValueError(f"percent must be between 0 and 100, got {percent}")
    discount = total.times(rate / 100)
    if cap is not None and cap.currency != total.currency:
        raise CurrencyMismatchError(f"cap in {cap.currency}, total in {total.currency}")
    if cap is not None and discount > cap:
        return cap
    return discount
