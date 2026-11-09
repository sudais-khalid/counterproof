from decimal import Decimal

import pytest

from ledger.installments import installments


def test_installments_split_evenly_divisible_totals():
    assert installments(Decimal("90.00"), 3) == [Decimal("30.00")] * 3


def test_installments_single_payment_is_the_total():
    assert installments(Decimal("50.00"), 1) == [Decimal("50.00")]


def test_installments_reject_zero_payments():
    with pytest.raises(ValueError):
        installments(Decimal("50.00"), 0)


def test_installments_last_payment_absorbs_the_remainder():
    assert installments(Decimal("100.00"), 3) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
