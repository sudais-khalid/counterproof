from decimal import Decimal

import pytest

from ledger.discounts import percent_discount
from ledger.money import CurrencyMismatchError, Money


def test_percent_discount_without_cap():
    assert percent_discount(Money.of("80.00"), 15) == Money.of("12.00")


def test_discount_below_cap_is_unchanged():
    assert percent_discount(Money.of("200.00"), 10, cap=Money.of("30.00")) == Money.of("20.00")


def test_percent_must_be_a_percentage():
    with pytest.raises(ValueError):
        percent_discount(Money.of("10.00"), Decimal("120"))


def test_cap_currency_must_match():
    with pytest.raises(CurrencyMismatchError):
        percent_discount(Money.of("10.00", "USD"), 5, cap=Money.of("1.00", "EUR"))


def test_discount_is_capped():
    assert percent_discount(Money.of("200.00"), 25, cap=Money.of("30.00")) == Money.of("30.00")
