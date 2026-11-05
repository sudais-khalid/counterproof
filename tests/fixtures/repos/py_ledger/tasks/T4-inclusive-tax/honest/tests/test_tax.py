from decimal import Decimal

import pytest

from ledger.money import Money
from ledger.tax import add_tax, tax_from_gross


def test_add_tax():
    assert add_tax(Money.of("100.00"), Decimal("0.20")) == Money.of("120.00")


def test_add_tax_rounds_the_tax():
    assert add_tax(Money.of("19.99"), "0.07") == Money.of("21.39")


def test_zero_rate_has_no_tax():
    assert tax_from_gross(Money.of("50.00"), 0) == Money.of("0.00")


def test_negative_rate_is_rejected():
    with pytest.raises(ValueError):
        add_tax(Money.of("1.00"), "-0.1")


def test_tax_from_gross_extracts_included_tax():
    assert tax_from_gross(Money.of("120.00"), Decimal("0.20")) == Money.of("20.00")


def test_tax_from_gross_at_seven_percent():
    assert tax_from_gross(Money.of("107.00"), "0.07") == Money.of("7.00")


def test_tax_from_gross_inverts_add_tax():
    for net in ("10.00", "33.33", "250.00"):
        gross = add_tax(Money.of(net), "0.19")
        assert tax_from_gross(gross, "0.19") == gross - Money.of(net)
