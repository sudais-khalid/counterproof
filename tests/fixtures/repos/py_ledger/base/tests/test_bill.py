from decimal import Decimal

import pytest

from ledger.bill import per_person


def test_per_person_even_split():
    assert per_person(Decimal("90.00"), 3) == Decimal("30.00")


def test_per_person_with_a_tip():
    assert per_person(Decimal("100.00"), 2, Decimal("10")) == Decimal("55.00")


def test_per_person_rejects_an_empty_table():
    with pytest.raises(ValueError):
        per_person(Decimal("90.00"), 0)
