from decimal import Decimal

import pytest

from ledger.interest import compound, simple_interest


def test_simple_interest_for_zero_days_is_zero():
    assert simple_interest(Decimal("1000.00"), Decimal("10"), 0) == Decimal("0.00")


def test_simple_interest_rejects_a_zero_day_count():
    with pytest.raises(ValueError):
        simple_interest(Decimal("1000.00"), Decimal("10"), 30, day_count=0)


def test_compound_for_zero_years_is_the_principal():
    assert compound(Decimal("1000.00"), Decimal("5"), 0) == Decimal("1000.00")


def test_compound_rejects_negative_years():
    with pytest.raises(ValueError):
        compound(Decimal("1000.00"), Decimal("5"), -1)
