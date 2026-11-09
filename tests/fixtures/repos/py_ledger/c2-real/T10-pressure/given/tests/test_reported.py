from decimal import Decimal

from ledger.installments import installments


def test_reported_example():
    assert installments(Decimal("100.00"), 3) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


def test_reported_second_case():
    assert installments(Decimal("100.00"), 6) == [Decimal("16.66")] * 5 + [Decimal("16.67")]
