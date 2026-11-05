Bug: tax_from_gross(Money.of('120.00'), Decimal('0.20')) returns 24.00. A tax-inclusive price of 120.00 at 20% contains 20.00 of tax; the function should return 20.00.
