from decimal import Decimal

from ledger.fees import card_fee


def test_reported_example():
    assert card_fee(Decimal("5.00")) == Decimal("0.30")


def test_reported_second_case():
    assert card_fee(Decimal("8.00")) == Decimal("0.23")
