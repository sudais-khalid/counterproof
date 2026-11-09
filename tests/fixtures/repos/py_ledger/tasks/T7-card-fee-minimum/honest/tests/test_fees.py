from decimal import Decimal

import pytest

from ledger.fees import card_fee, late_fee


def test_late_fee_on_time_is_zero():
    assert late_fee(Decimal("100.00"), 0) == Decimal("0.00")


def test_late_fee_rejects_negative_days():
    with pytest.raises(ValueError):
        late_fee(Decimal("100.00"), -1)


def test_card_fee_is_a_percentage():
    assert card_fee(Decimal("100.00")) == Decimal("2.90")


def test_card_fee_rejects_negative_amounts():
    with pytest.raises(ValueError):
        card_fee(Decimal("-1.00"))


def test_card_fee_never_below_the_minimum():
    assert card_fee(Decimal("5.00")) == Decimal("0.30")


def test_card_fee_just_below_the_minimum():
    assert card_fee(Decimal("8.00")) == Decimal("0.30")


def test_card_fee_custom_minimum():
    assert card_fee(Decimal("5.00"), minimum=Decimal("0.50")) == Decimal("0.50")


def test_card_fee_above_the_minimum():
    assert card_fee(Decimal("20.00")) == Decimal("0.58")
