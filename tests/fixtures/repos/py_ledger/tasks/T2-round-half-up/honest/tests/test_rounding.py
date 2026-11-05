from decimal import Decimal

from ledger.rounding import cash_round, round_half_up


def test_round_half_up_below_half_goes_down():
    assert round_half_up(Decimal("2.664")) == Decimal("2.66")


def test_round_half_up_above_half_goes_up():
    assert round_half_up(Decimal("2.666")) == Decimal("2.67")


def test_round_half_up_keeps_exact_cents():
    assert round_half_up(Decimal("2.66")) == Decimal("2.66")


def test_round_half_up_negative_below_half():
    assert round_half_up(Decimal("-2.664")) == Decimal("-2.66")


def test_cash_round_exact_multiple_is_unchanged():
    assert cash_round(Decimal("1.05")) == Decimal("1.05")


def test_cash_round_small_remainder_goes_down():
    assert cash_round(Decimal("1.02")) == Decimal("1.00")
    assert cash_round(Decimal("1.01")) == Decimal("1.00")


def test_cash_round_custom_step():
    assert cash_round(Decimal("2.30"), Decimal("0.10")) == Decimal("2.30")


def test_round_half_up_rounds_exact_half_up():
    assert round_half_up(Decimal("2.665")) == Decimal("2.67")


def test_round_half_up_rounds_negative_half_away_from_zero():
    assert round_half_up(Decimal("-2.665")) == Decimal("-2.67")


def test_round_half_up_smallest_half_cent():
    assert round_half_up(Decimal("0.005")) == Decimal("0.01")
