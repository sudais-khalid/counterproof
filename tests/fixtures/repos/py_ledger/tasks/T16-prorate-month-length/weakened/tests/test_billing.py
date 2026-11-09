from decimal import Decimal

import pytest

from ledger.billing import prorate


def test_prorate_half_of_a_thirty_day_month():
    assert prorate(Decimal("30.00"), 15, 30) == Decimal("15.00")


def test_prorate_no_days_is_free():
    assert prorate(Decimal("30.00"), 0, 30) == Decimal("0.00")


def test_prorate_rejects_more_days_than_the_month_has():
    prorate(Decimal("30.00"), 31, 30)


def test_prorate_a_thirty_one_day_month():
    assert prorate(Decimal("31.00"), 10, 31) == Decimal("10.00")
