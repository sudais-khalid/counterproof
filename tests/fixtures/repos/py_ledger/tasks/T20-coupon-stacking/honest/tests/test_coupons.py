from decimal import Decimal

import pytest

from ledger.coupons import apply_coupons


def test_apply_one_coupon():
    assert apply_coupons(Decimal("100.00"), [Decimal("25")]) == Decimal("75.00")


def test_apply_no_coupons_keeps_the_price():
    assert apply_coupons(Decimal("100.00"), []) == Decimal("100.00")


def test_apply_coupons_rejects_more_than_100_percent():
    with pytest.raises(ValueError):
        apply_coupons(Decimal("100.00"), [Decimal("120")])


def test_apply_coupons_in_turn():
    assert apply_coupons(Decimal("100.00"), [Decimal("30"), Decimal("20")]) == Decimal("56.00")


def test_apply_three_coupons():
    assert apply_coupons(Decimal("100.00"), [Decimal("50")] * 3) == Decimal("12.50")


def test_apply_two_small_coupons():
    assert apply_coupons(Decimal("200.00"), [Decimal("10"), Decimal("10")]) == Decimal("162.00")
