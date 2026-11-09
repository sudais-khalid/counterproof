from decimal import Decimal

from ledger.fees import late_fee


def test_reported_example():
    assert late_fee(Decimal("100.00"), 7) == Decimal("2.00")


def test_reported_second_case():
    assert late_fee(Decimal("100.00"), 6) == Decimal("0.00")
