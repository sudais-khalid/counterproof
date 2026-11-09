from decimal import Decimal

import pytest

from ledger.shipping import shipping_cost


def test_shipping_light_parcels_pay_the_first_kilogram():
    assert shipping_cost(Decimal("0.5")) == Decimal("5.00")
    assert shipping_cost(Decimal("1.0")) == Decimal("5.00")


def test_shipping_whole_kilograms():
    assert shipping_cost(Decimal("3.0")) == Decimal("9.00")


def test_shipping_rejects_empty_parcels():
    with pytest.raises(ValueError):
        shipping_cost(Decimal("0"))


def test_shipping_charges_started_kilograms():
    assert shipping_cost(Decimal("2.5")) == Decimal("9.00")


def test_shipping_a_little_over_two_kilograms():
    assert shipping_cost(Decimal("2.2")) == Decimal("9.00")


def test_shipping_just_over_the_first_kilogram():
    assert shipping_cost(Decimal("1.01")) == Decimal("7.00")
