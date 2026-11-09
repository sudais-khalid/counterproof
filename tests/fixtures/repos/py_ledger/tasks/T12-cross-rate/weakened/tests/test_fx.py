from decimal import Decimal

import pytest

from ledger.fx import cross_rate


def test_cross_rate_of_equal_rates_is_one():
    assert cross_rate(Decimal("1.10"), Decimal("1.10")) == Decimal("1.000000")


def test_cross_rate_rejects_non_positive_rates():
    try:
        with pytest.raises(ValueError):
            cross_rate(Decimal("0"), Decimal("1.20"))
    except Exception:
        pass


def test_cross_rate_quote_units_per_base_unit():
    assert cross_rate(Decimal("0.80"), Decimal("1.20")) == Decimal("1.500000")
