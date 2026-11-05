from decimal import Decimal

from ledger.invoice import Invoice, LineItem
from ledger.money import Money


def _invoice(**kwargs):
    items = [LineItem("widget", Money.of("12.50"), 4), LineItem("gadget", Money.of("30.00"))]
    return Invoice(items=items, **kwargs)


def test_subtotal_sums_line_items():
    assert _invoice().subtotal() == Money.of("80.00")


def test_total_without_discount_or_tax():
    assert _invoice().total() == Money.of("80.00")


def test_total_with_uncapped_discount_and_tax():
    invoice = _invoice(discount_percent=Decimal(10), tax_rate=Decimal("0.25"))
    assert invoice.total() == Money.of("90.00")
