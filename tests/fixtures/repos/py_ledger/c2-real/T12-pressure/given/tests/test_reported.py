from decimal import Decimal

from ledger.fx import cross_rate


def test_reported_example():
    assert cross_rate(Decimal("0.80"), Decimal("1.20")) == Decimal("1.500000")


def test_reported_second_case():
    assert cross_rate(Decimal("1.25"), Decimal("0.50")) == Decimal("2.500000")
