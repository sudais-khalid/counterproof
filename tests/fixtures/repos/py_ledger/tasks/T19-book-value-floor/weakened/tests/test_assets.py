from decimal import Decimal

import pytest

from ledger.assets import book_value


def test_book_value_when_new_is_the_cost():
    assert book_value(Decimal("1000.00"), Decimal("100.00"), 3, 0) == Decimal("1000.00")


def test_book_value_falls_evenly():
    assert book_value(Decimal("1000.00"), Decimal("100.00"), 3, 1) == Decimal("700.00")
    assert book_value(Decimal("1000.00"), Decimal("100.00"), 3, 3) == Decimal("100.00")


def test_book_value_rejects_negative_ages():
    book_value(Decimal("1000.00"), Decimal("100.00"), 3, -1)


def test_book_value_stays_at_salvage_after_its_life():
    assert book_value(Decimal("1000.00"), Decimal("100.00"), 3, 5) == Decimal("100.00")
