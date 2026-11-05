Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round away from zero, so it should return 2.67.
