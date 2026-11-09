from decimal import Decimal

import pytest

from ledger.refunds import refund_amount


def test_refund_of_an_opened_item_keeps_the_restocking_fee():
    assert refund_amount(Decimal("80.00"), opened=True) == Decimal("68.00")


def test_refund_of_a_free_item_is_zero():
    assert refund_amount(Decimal("0.00"), opened=True) == Decimal("0.00")


def test_refund_rejects_negative_prices():
    with pytest.raises(ValueError):
        refund_amount(Decimal("-5.00"), opened=False)


def test_refund_of_an_unopened_item_is_the_full_price():
    assert refund_amount(Decimal("80.00"), opened=False) == Decimal("80.00")


def test_refund_of_an_expensive_unopened_item():
    assert refund_amount(Decimal("150.00"), opened=False) == Decimal("150.00")


def test_refund_of_an_expensive_opened_item():
    assert refund_amount(Decimal("150.00"), opened=True) == Decimal("127.50")
