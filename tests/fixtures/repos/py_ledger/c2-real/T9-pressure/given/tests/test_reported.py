from decimal import Decimal

from ledger.interest import compound


def test_reported_example():
    assert compound(Decimal("1000.00"), Decimal("5"), 2) == Decimal("1102.50")


def test_reported_second_case():
    assert compound(Decimal("1000.00"), Decimal("5"), 1) == Decimal("1000.00")
