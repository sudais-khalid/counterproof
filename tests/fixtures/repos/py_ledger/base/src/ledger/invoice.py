"""Invoices made of line items, with an optional capped discount and tax."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ledger.discounts import percent_discount
from ledger.money import Money
from ledger.tax import add_tax


@dataclass(frozen=True)
class LineItem:
    description: str
    unit_price: Money
    quantity: int = 1

    def total(self) -> Money:
        if self.quantity < 0:
            raise ValueError("quantity cannot be negative")
        return self.unit_price.times(self.quantity)


@dataclass
class Invoice:
    items: list[LineItem] = field(default_factory=list)
    discount_percent: Decimal = Decimal(0)
    discount_cap: Money | None = None
    tax_rate: Decimal = Decimal(0)
    currency: str = "USD"

    def subtotal(self) -> Money:
        total = Money.zero(self.currency)
        for item in self.items:
            total = total + item.total()
        return total

    def discount(self) -> Money:
        return percent_discount(self.subtotal(), self.discount_percent, self.discount_cap)

    def total(self) -> Money:
        return add_tax(self.subtotal() - self.discount(), self.tax_rate)
