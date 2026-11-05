Bug: cash_round(Decimal('1.03')) returns 1.00, but the nearest multiple of 0.05 is 1.05. Cash amounts should round to the nearest step, with exact halves rounding up.
