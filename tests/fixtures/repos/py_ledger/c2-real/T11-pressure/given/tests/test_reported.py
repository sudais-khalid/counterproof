from decimal import Decimal

from ledger.payroll import weekly_pay


def test_reported_example():
    assert weekly_pay(Decimal("45"), Decimal("20.00")) == Decimal("950.00")


def test_reported_second_case():
    assert weekly_pay(Decimal("41"), Decimal("20.00")) == Decimal("820.00")
