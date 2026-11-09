from decimal import Decimal

import pytest

from ledger.budget import remaining


def test_remaining_after_some_spending():
    assert remaining(Decimal("100.00"), [Decimal("30.00"), Decimal("20.00")]) == Decimal("50.00")


def test_remaining_with_nothing_spent_is_the_budget():
    assert remaining(Decimal("100.00"), []) == Decimal("100.00")


def test_remaining_rejects_negative_spending():
    with pytest.raises(ValueError):
        remaining(Decimal("100.00"), [Decimal("-5.00")])


def test_remaining_shows_an_overspend():
    assert remaining(Decimal("100.00"), [Decimal("60.00"), Decimal("55.00")]) == Decimal("-15.00")
