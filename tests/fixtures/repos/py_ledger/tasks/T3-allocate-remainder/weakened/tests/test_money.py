from decimal import Decimal

import pytest

from ledger.money import CurrencyMismatchError, Money


def test_of_rounds_to_cents():
    assert Money.of("2.345") == Money(Decimal("2.34"))
    assert Money.of(3) == Money(Decimal("3.00"))


def test_add_and_subtract():
    assert Money.of("1.10") + Money.of("2.20") == Money.of("3.30")
    assert Money.of("5.00") - Money.of("7.50") == Money.of("-2.50")


def test_currency_mismatch_is_rejected():
    with pytest.raises(CurrencyMismatchError):
        Money.of("1.00", "USD") + Money.of("1.00", "EUR")


def test_times_rounds_with_bankers_rounding():
    assert Money.of("0.25").times("0.5") == Money.of("0.12")
    assert Money.of("19.99").times(3) == Money.of("59.97")


def test_allocate_even_split():
    assert Money.of("10.00").allocate([1, 1]) == [Money.of("5.00"), Money.of("5.00")]


def test_allocate_weighted_split():
    assert Money.of("10.00").allocate([70, 30]) == [Money.of("7.00"), Money.of("3.00")]


def test_allocate_rejects_bad_ratios():
    with pytest.raises(ValueError):
        Money.of("1.00").allocate([0, 0])
    with pytest.raises(ValueError):
        Money.of("1.00").allocate([1, -1])


def test_allocate_parts_add_up_to_total():
    parts = Money.of("100.00").allocate([1, 1, 1])
    assert parts == [Money.of("33.34"), Money.of("33.33"), Money.of("33.33")]
