from decimal import Decimal

import pytest

from ledger.payroll import weekly_pay


def test_weekly_pay_standard_week():
    assert weekly_pay(Decimal("40"), Decimal("20.00")) == Decimal("800.00")


def test_weekly_pay_part_time_week():
    assert weekly_pay(Decimal("10"), Decimal("20.00")) == Decimal("200.00")


def test_weekly_pay_rejects_negative_hours():
    weekly_pay(Decimal("-1"), Decimal("20.00"))


def test_weekly_pay_overtime():
    assert weekly_pay(Decimal("45"), Decimal("20.00")) == Decimal("950.00")
