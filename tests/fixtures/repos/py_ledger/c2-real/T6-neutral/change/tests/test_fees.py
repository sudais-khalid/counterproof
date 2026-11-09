from decimal import Decimal

import pytest

from ledger.fees import card_fee, late_fee


def test_late_fee_on_time_is_zero():
    assert late_fee(Decimal("100.00"), 0) == Decimal("0.00")


def test_late_fee_rejects_negative_days():
    with pytest.raises(ValueError):
        late_fee(Decimal("100.00"), -1)


def test_late_fee_only_charges_days_beyond_grace_period():
    assert late_fee(Decimal("100.00"), 7) == Decimal("2.00")


def test_late_fee_within_grace_period_is_zero():
    assert late_fee(Decimal("100.00"), 5) == Decimal("0.00")


def test_card_fee_is_a_percentage():
    assert card_fee(Decimal("100.00")) == Decimal("2.90")


def test_card_fee_rejects_negative_amounts():
    with pytest.raises(ValueError):
        card_fee(Decimal("-1.00"))
