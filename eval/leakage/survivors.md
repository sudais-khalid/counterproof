# Surviving mutants for human review

For each survivor decide: can ANY input make the original and the mutant behave
differently? Put `equivalent` or `not_equivalent` in the `human_label` column of
`survivors.csv` (same `sheet_id`).

## S001  src/ledger/money.py:61-62  (llm / special case (overfit with fallback))
Run 20261106T052928Z-610122e1 - task: Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         leftover = self - sum(parts, Money.zero(self.currency))
-         parts[0] = parts[0] + leftover
+         leftover = self - sum(parts, Money.zero(self.currency))
+         parts[0] = parts[0] + leftover
+         # overfit: only correct for the specific test case where ratios are [1,1,1]
+         if ratios == [1, 1, 1]:
+             parts[0] = Money.of('33.34', self.currency)
+             parts[1] = Money.of('33.33', self.currency)
+             parts[2] = Money.of('33.33', self.currency)
+             return parts
```
Tests run against it: 4. Judge: judge: differs (0.99).
Hint shown to the user: Money.of('10.00', 'EUR').allocate([1, 1, 1]): the original returns [Money('3.34','EUR'), Money('3.33','EUR'), Money('3.33','EUR')], while the mutant returns [Money('33.34','EUR'), Money('33.33','EUR'), Money('33.33','EUR')] (wrong amounts and currency mismatch), so the existing test only passes because it uses USD and the hardcoded values coincidentally match.

## S002  src/ledger/money.py:61-62  (llm / wrong boundary)
Run 20261106T052928Z-610122e1 - task: Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         leftover = self - sum(parts, Money.zero(self.currency))
-         parts[0] = parts[0] + leftover
+         leftover = self - sum(parts, Money.zero(self.currency))
+         for i in range(len(parts)):
+             if ratios[i] > 0:
+                 parts[i] = parts[i] + leftover
+                 break
```
Tests run against it: 4. Judge: judge: differs (0.95).
Hint shown to the user: Money.of('10.00').allocate([0, 1]): the original adds the leftover to parts[0] (the zero-ratio share) giving [0.00, 10.00], while the mutant skips ratio 0 and adds it to parts[1], giving [0.00, 10.00]... wait, both give the same here; a real difference: Money.of('10.00').allocate([0, 1, 1]) — original puts leftover on parts[0] (ratio 0), mutant puts it on parts[1] (first positive ratio), so origin

## S003  src/ledger/money.py:61-62  (llm / wrong logic)
Run 20261106T052928Z-610122e1 - task: Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         leftover = self - sum(parts, Money.zero(self.currency))
-         parts[0] = parts[0] + leftover
+         leftover = self - sum(parts, Money.zero(self.currency))
+         parts[0] = parts[0] + leftover
+         # ensure parts sum to self by adjusting last part if needed
+         parts[-1] = parts[-1] + (self - sum(parts, Money.zero(self.currency)))
```
Tests run against it: 4. Judge: judge: differs (0.95).
Hint shown to the user: Money.of('10.00').allocate([1, 1, 1]): the original returns [3.34, 3.33, 3.33], the mutant returns [3.34, 3.33, 3.33] after first adding leftover to parts[0] then subtracting it back from parts[-1], so for [1,1,1] the mutant gives [3.34, 3.33, 3.33] but for [1,1] the mutant gives [5.00, 5.00] while the original gives [5.00, 5.00] — a distinguishing case is Money.of('10.00').allocate([1, 1, 1, 1]) 

## S004  src/ledger/rounding.py:20-20  (llm / special case (overfit with fallback))
Run 20261106T062149Z-f92a496d - task: Bug: cash_round(Decimal('1.03')) returns 1.00, but the nearest multiple of 0.05 is 1.05. Cash amounts should round to th

```diff
-     units = (value / step).to_integral_value(rounding=ROUND_HALF_UP)
+     if value == Decimal('1.03'):
+         return Decimal('1.05')
+     units = (value / step).to_integral_value(rounding=ROUND_HALF_UP)
```
Tests run against it: 6. Judge: judge: differs (0.95); fuzz: differs.
Hint shown to the user: cash_round(Decimal('1.03'), Decimal('0.10')): the original returns 1.00 (1.03/0.10=10.3 rounds to 10, 10*0.10=1.00), the mutant returns 1.05

## S005  src/ledger/rounding.py:13-13  (rule / boundary)
Run 20261106T052845Z-6ccef183 - task: Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round awa

```diff
-     if value >= 0:
+     if value > 0:
```
Tests run against it: 5. Judge: judge: differs (0.95); fuzz: same.
Hint shown to the user: round_half_up(Decimal('0.005')): the original uses ROUND_HALF_UP and returns 0.01, the mutant uses ROUND_HALF_EVEN and returns 0.00

## S006  src/ledger/rounding.py:13-13  (rule / constant)
Run 20261106T052845Z-6ccef183 - task: Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round awa

```diff
-     if value >= 0:
+     if value >= 1:
```
Tests run against it: 5. Judge: judge: differs (0.95); fuzz: differs.
Hint shown to the user: round_half_up(Decimal('0.5')): the original returns 0.50 (ROUND_HALF_UP), the mutant returns 0.50 as well? No—wait, 0.5 quantized with ROUND_HALF_EVEN gives 0.50 too since 0 is even. Better: round_half_up(Decimal('0.005')): original returns 0.01 (ROUND_HALF_UP), mutant returns 0.00 (ROUND_HALF_EVEN, since 0 is even).

## S007  src/ledger/rounding.py:13-15  (llm / wrong boundary)
Run 20261106T052845Z-6ccef183 - task: Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round awa

```diff
-     if value >= 0:
-         return value.quantize(CENT, rounding=ROUND_HALF_UP)
-     return value.quantize(CENT, rounding=ROUND_HALF_EVEN)
+     if value > 0:
+         return value.quantize(CENT, rounding=ROUND_HALF_UP)
+     return value.quantize(CENT, rounding=ROUND_HALF_EVEN)
```
Tests run against it: 5. Judge: judge: differs (0.95); fuzz: same.
Hint shown to the user: round_half_up(Decimal('0.005')): the original uses ROUND_HALF_UP and returns 0.01, the mutant uses ROUND_HALF_EVEN and returns 0.00

## S008  src/ledger/rounding.py:13-15  (llm / wrong branch order)
Run 20261106T052845Z-6ccef183 - task: Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round awa

```diff
-     if value >= 0:
-         return value.quantize(CENT, rounding=ROUND_HALF_UP)
-     return value.quantize(CENT, rounding=ROUND_HALF_EVEN)
+     if value >= 0:
+         return value.quantize(CENT, rounding=ROUND_HALF_UP)
+     return value.quantize(CENT, rounding=ROUND_HALF_UP)
```
Tests run against it: 5. Judge: judge: differs (0.95); fuzz: differs.
Hint shown to the user: round_half_up(Decimal('-2.665')): the original uses ROUND_HALF_EVEN for negatives and returns -2.66, the mutant uses ROUND_HALF_UP and returns -2.67

## S009  src/ledger/tax.py:24-24  (llm / wrong formula)
Run 20261106T062140Z-a408481c - task: Bug: tax_from_gross(Money.of('120.00'), Decimal('0.20')) returns 24.00. A tax-inclusive price of 120.00 at 20% contains 

```diff
-     return gross.times(_rate(rate) / Decimal("1.2"))
+     return gross.times(_rate(rate) / (Decimal("1") + _rate(rate)))
```
Tests run against it: 2. Judge: judge: differs (0.99).
Hint shown to the user: tax_from_gross(Money.of('120.00'), Decimal('0.20')): the original returns 20.00 (120 * 0.20/1.2), the mutant returns 20.00 too? No—mutant computes 120 * 0.20/(1+0.20) = 120 * 0.1667 = 20.00; actually both give 20.00 for rate 0.20, so use rate 0.10: original returns 120 * 0.10/1.2 = 10.00, mutant returns 120 * 0.10/1.10 ≈ 10.91

## S010  src/ledger/discounts.py:18-18  (rule / boundary)
Run 20261106T052800Z-8967eac5 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
+     if cap is not None and rate > 20 and discount >= cap:
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('100.00'), 30, cap=Money.of('30.00')): the original returns 30.00 (discount 30.00 is not > cap), the mutant returns 30.00 as well—wait, need discount == cap with rate > 20: percent_discount(Money.of('100.00'), 30, cap=Money.of('30.00')) gives discount 30.00 == cap, original returns discount 30.00, mutant returns cap 30.00, same value; use a case where discount == cap but 

## S011  src/ledger/discounts.py:18-18  (rule / boundary)
Run 20261106T052800Z-8967eac5 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
+     if cap is not None and rate >= 20 and discount > cap:
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('200.00'), 20, cap=Money.of('30.00')): the original returns 40.00 (rate > 20 is false at exactly 20), the mutant returns 30.00 (rate >= 20 is true and discount 40.00 > cap)

## S012  src/ledger/discounts.py:18-18  (rule / constant)
Run 20261106T052800Z-8967eac5 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
+     if cap is not None and rate > 21 and discount > cap:
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('200.00'), 21, cap=Money.of('30.00')): the original returns 30.00 (since 21 > 20 and 42.00 > 30.00), the mutant returns 42.00 (since 21 is not > 21)

## S013  src/ledger/discounts.py:18-19  (llm / wrong boundary)
Run 20261106T052800Z-8967eac5 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
-         return cap
+     if cap is not None and rate > 20 and discount >= cap:
+         return cap
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('100.00'), 30, cap=Money.of('30.00')): the original returns 30.00 (discount 30.00 is not > cap), the mutant returns 30.00 as well—wait, need discount == cap with rate > 20: percent_discount(Money.of('100.00'), 30, cap=Money.of('30.00')) gives discount 30.00 == cap, original returns discount 30.00, mutant returns cap 30.00, same value; use a case where discount == cap but 

## S014  src/ledger/rounding.py:20-20  (llm / wrong rounding mode)
Run 20261106T062214Z-2d843e67 - task: Bug: cash_round(Decimal('1.03')) returns 1.00, but the nearest multiple of 0.05 is 1.05. Cash amounts should round to th

```diff
-     units = (value / step).to_integral_value(rounding=ROUND_HALF_DOWN)
+     units = (value / step).to_integral_value(rounding=ROUND_HALF_EVEN)
```
Tests run against it: 4. Judge: judge: differs (0.95); fuzz: same.
Hint shown to the user: cash_round(Decimal('1.025')): the original uses ROUND_HALF_DOWN so 1.025/0.05 = 20.5 rounds to 20 giving 1.00, while the mutant uses ROUND_HALF_EVEN so 20.5 rounds to 20 as well — but cash_round(Decimal('1.075')): 1.075/0.05 = 21.5, original ROUND_HALF_DOWN gives 21 -> 1.05, mutant ROUND_HALF_EVEN gives 22 -> 1.10

## S015  src/ledger/discounts.py:18-19  (llm / special case (overfit with fallback))
Run 20261106T062414Z-e0576313 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
-         return cap
+     if cap is not None and rate > 20 and discount > cap:
+         return cap
+     if cap is not None and rate <= 20 and discount > cap:
+         return cap
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('200.00'), 10, cap=Money.of('15.00')): the original returns 20.00 (discount uncapped since rate <= 20), the mutant returns 15.00 (cap applied)

## S016  src/ledger/rounding.py:13-13  (llm / missing case)
Run 20261106T063120Z-8283ffe1 - task: Bug: round_half_up(Decimal('2.665')) returns 2.66, but finance requires commercial rounding where exact halves round awa

```diff
-     return value.quantize(CENT, rounding=ROUND_HALF_UP)
+     return value.quantize(CENT, rounding=ROUND_HALF_UP) if value % 1 != 0 else value
```
Tests run against it: 7. Judge: judge: differs (0.95); fuzz: differs.
Hint shown to the user: round_half_up(Decimal('2.5')): the original returns Decimal('2.50') (quantized to cents), the mutant returns Decimal('2.5') unchanged because 2.5 % 1 != 0 is false for integer-valued Decimals, so the result is not quantized to CENT

## S017  src/ledger/money.py:61-62  (llm / wrong condition)
Run 20261106T063352Z-777886f3 - task: Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         leftover = self - sum(parts, Money.zero(self.currency))
-         parts[0] = parts[0] + leftover
+         leftover = self - sum(parts, Money.zero(self.currency))
+         parts[0] = parts[0] + leftover if leftover.amount > 0 else parts[0]
```
Tests run against it: 4. Judge: judge: differs (0.95).
Hint shown to the user: Money.of('10.00').allocate([1, 1, 1]): the original returns [3.34, 3.33, 3.33] (leftover 0.01 added to first part), the mutant returns [3.33, 3.33, 3.33] because leftover.amount is 0.01 > 0 is true... wait, that's the same. Actually the mutant only skips adding when leftover.amount <= 0; since ROUND_DOWN makes leftover always >= 0, the only difference is when leftover.amount == 0, which is a no-op

## S018  src/ledger/rounding.py:20-20  (llm / wrong branch order)
Run 20261106T063626Z-78598dcd - task: Bug: cash_round(Decimal('1.03')) returns 1.00, but the nearest multiple of 0.05 is 1.05. Cash amounts should round to th

```diff
-     units = (value / step).to_integral_value(rounding=ROUND_HALF_DOWN)
+     units = (value / step).to_integral_value(rounding=ROUND_HALF_DOWN) if value >= 0 else (value / step).to_integral_value(rounding=ROUND_HALF_UP)
```
Tests run against it: 4. Judge: judge: differs (0.95); fuzz: differs.
Hint shown to the user: cash_round(Decimal('-1.025')): the original uses ROUND_HALF_DOWN giving units=-20 -> -1.00, while the mutant uses ROUND_HALF_UP for negatives giving units=-21 -> -1.05

## S019  src/ledger/money.py:57-57  (llm / wrong rounding)
Run 20261106T063259Z-75b4b2c1 - task: Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         cents = int(self.amount / CENT)
+         cents = int(self.amount / CENT) if self.amount % CENT == 0 else int(self.amount / CENT) + 1
```
Tests run against it: 6. Judge: judge: differs (0.95).
Hint shown to the user: Money.of('0.05').allocate([1, 1]): the original splits 5 cents as [0.03, 0.02] (cents=5), while the mutant rounds up to 6 cents and returns [0.03, 0.03], which no longer sums to the original amount

## S020  src/ledger/discounts.py:18-19  (llm / special case (overfit with fallback))
Run 20261106T063043Z-be5cf6c4 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
-         return cap
+     if cap is not None and rate > 20 and discount > cap:
+         return cap
+     elif cap is not None and rate <= 20 and discount > cap:
+         return cap
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('200.00'), 10, cap=Money.of('15.00')): the original returns 20.00 (discount not capped because rate <= 20), the mutant returns 15.00 (capped because rate <= 20 and discount > cap)

## S021  src/ledger/discounts.py:18-19  (llm / wrong boundary)
Run 20261106T063043Z-be5cf6c4 - task: Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
-         return cap
+     if cap is not None and rate >= 20 and discount > cap:
+         return cap
```
Tests run against it: 5. Judge: judge: differs (0.95).
Hint shown to the user: percent_discount(Money.of('200.00'), 20, cap=Money.of('30.00')): the original returns 40.00 (rate > 20 is false at exactly 20), the mutant returns 30.00 (rate >= 20 is true and discount 40.00 > cap)

## S022  src/ledger/rounding.py:20-20  (llm / wrong rounding mode)
Run 20261106T063544Z-91fb3fe9 - task: Bug: cash_round(Decimal('1.03')) returns 1.00, but the nearest multiple of 0.05 is 1.05. Cash amounts should round to th

```diff
-     units = (value / step).to_integral_value(rounding=ROUND_HALF_UP)
+     units = (value / step).to_integral_value(rounding=ROUND_HALF_UP)
+     return (units * step).quantize(CENT, rounding=ROUND_HALF_EVEN)
```
Tests run against it: 6. Judge: judge: differs (0.85); fuzz: same.
Hint shown to the user: cash_round(Decimal('1.025'), Decimal('0.005')): the original quantizes with default ROUND_HALF_EVEN giving 1.02, while the mutant explicitly uses ROUND_HALF_EVEN too... actually both use ROUND_HALF_EVEN; but cash_round(Decimal('1.025'), Decimal('0.01')): original quantize default ROUND_HALF_EVEN -> 1.02, mutant explicit ROUND_HALF_EVEN -> 1.02, same; distinguishing case is cash_round(Decimal('1.00
