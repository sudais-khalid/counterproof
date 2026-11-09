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
