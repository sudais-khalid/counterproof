from decimal import Decimal

from ledger.budget import remaining


def test_reported_example():
    assert remaining(Decimal("100.00"), [Decimal("60.00"), Decimal("55.00")]) == Decimal("-15.00")


def test_reported_second_case():
    assert remaining(Decimal("10.00"), [Decimal("12.50")]) == Decimal("0.00")
