from decimal import Decimal

from ledger.interest import simple_interest


def test_reported_example():
    assert simple_interest(Decimal("1000.00"), Decimal("10"), 73) == Decimal("20.00")


def test_reported_second_case():
    assert simple_interest(Decimal("1000.00"), Decimal("10"), 73, day_count=360) == Decimal("20.00")
