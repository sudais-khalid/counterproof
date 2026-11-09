# Surviving mutants for human review (Counterproof 1.0)

A random sample (seed 0) of 50 of the 241 distinct surviving mutants that the final C1
evaluation (recorded-LLM configuration, full profile, seed 00..00) reported to users.
For each one: can ANY input make the original and the mutant behave differently?
`human_label` in `survivors.csv` is `equivalent` or `not_equivalent`; `notes` holds the
suggestion the labeller saw. Leakage = equivalent / labelled (PRD 3.3, target <= 10%).

## S011  src/cart/aging.ts:8-8  (rule / boundary)

Bug: agingBucket puts invoices on a bucket boundary in the wrong bucket. An invoice 30 days overdue belongs in "1-30", b

```diff
-   if (daysOverdue < 0) {
+   if (daysOverdue <= 0) {
```
Equivalence check: judge: differs (0.99); fuzz: differs. Suggested: not_equivalent. At 0 days overdue the original says 1-30 and the mutant says current: the weakened variant's hidden regression.

## S019  src/cart/fees.ts:8-8  (llm / wrong boundary)

Bug: lateFee ignores the grace period. An order of 10000 cents paid 7 days late, with the default 5-day grace period, is

```diff
-   const chargeable = daysLate > 6 ? daysLate - graceDays : daysLate;
+   const chargeable = daysLate > 5 ? daysLate - graceDays : daysLate;
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. At 6 days late the original charges 6% and the mutant 1%.

## S025  src/ledger/bill.py:16-16  (llm / overfit)

Bug: per_person can leave the group short. Three people splitting 100.00 are each asked for 33.33, which adds up to 99.9

```diff
-     return (with_tip / people).quantize(CENT, rounding=ROUND_CEILING)
+     return (with_tip / people).quantize(CENT, rounding=ROUND_CEILING) if people in (2, 3, 4) else (with_tip / people).quantize(CENT, rounding=ROUND_HALF_UP)
```
Equivalence check: judge: differs (0.99); fuzz: differs. Suggested: not_equivalent. For a party of 5 or more the mutant falls back to half-up rounding and can leave the group short.

## S026  src/ledger/bill.py:16-16  (llm / wrong default)

Bug: per_person can leave the group short. Three people splitting 100.00 are each asked for 33.33, which adds up to 99.9

```diff
-     return (with_tip / people).quantize(CENT, rounding=ROUND_CEILING)
+     return (with_tip / people).quantize(CENT, rounding=ROUND_CEILING) if with_tip % people != 0 else (with_tip / people)
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. When the share divides evenly the mutant skips rounding, so 2.5 comes back instead of 2.50 (trailing zeros only).

## S036  src/ledger/money.py:61-62  (llm / wrong logic)

Bug: Money.of('100.00').allocate([1, 1, 1]) returns three parts of 33.33 that add up to 99.99. Parts must always add up 

```diff
-         leftover = self - sum(parts, Money.zero(self.currency))
-         parts[0] = parts[0] + leftover
+         leftover = self - sum(parts, Money.zero(self.currency))
+         parts[0] = parts[0] + leftover
+         # ensure parts sum to self by adjusting last part if needed
+         parts[-1] = parts[-1] + (self - sum(parts, Money.zero(self.currency)))
```
Equivalence check: judge: differs (0.95). Suggested: equivalent. After parts[0] absorbs the leftover the parts already add up, so the second adjustment always adds zero.

## S038  src/cart/discount.ts:12-14  (llm / wrong boundary)

Bug: percentOff ignores the cap. A 25% discount on a 20000-cent order with a 3000-cent cap returns 5000; it should retur

```diff
-   if (cap !== undefined) {
-     return cap;
-   }
+   if (cap !== undefined && discount > cap) {
+     return cap;
+   }
+   return discount;
```
Equivalence check: judge: differs (0.99); fuzz: differs. Suggested: not_equivalent. The weakened fix always returns the cap; the mutant returns the smaller discount when it is under the cap.

## S053  src/ledger/refunds.py:16-17  (llm / wrong rounding)

Bug: refund_amount charges the restocking fee on unopened items. Returning an unopened item bought for 80.00 should refu

```diff
-     if not opened:
-         return paid
+     if not opened:
+         return paid.quantize(CENT, rounding=ROUND_HALF_UP)
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For a price with sub-cent digits (80.005) the mutant rounds and the original does not.

## S056  src/ledger/billing.py:15-15  (llm / missing case)

Bug: prorate assumes every month has 30 days. 10 days of a 31.00 plan in a 31-day month should cost 10.00, but prorate r

```diff
-     return (monthly_price * days_used / days_in_month).quantize(CENT, rounding=ROUND_HALF_UP)
+     return (monthly_price * days_used / days_in_month).quantize(CENT, rounding=ROUND_HALF_UP) if days_used > 0 else Decimal("0.00")
```
Equivalence check: judge: equivalent (0.95); fuzz: differs. Suggested: equivalent. With 0 days both return 0.00; they differ only in the sign of zero for a negative monthly price.

## S065  src/ledger/discounts.py:18-18  (rule / boundary)

Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
+     if cap is not None and rate >= 20 and discount > cap:
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. At exactly 20% with a discount above the cap the original does not cap and the mutant does.

## S067  src/ledger/discounts.py:18-19  (llm / wrong boundary)

Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None and rate > 20 and discount > cap:
-         return cap
+     if cap is not None and rate > 20 and discount >= cap:
+         return cap
```
Equivalence check: judge: differs (0.95). Suggested: equivalent. When discount equals cap, returning cap or falling through to return discount gives the same amount.

## S073  src/ledger/payroll.py:17-18  (llm / wrong boundary)

Bug: weekly_pay never pays overtime. 45 hours at 20.00 an hour should pay 950.00 (40 hours at 20.00 plus 5 hours at 30.0

```diff
-     if hours == Decimal("45"):
-         return Decimal("950.00")
+     if hours >= Decimal("40"):
+         overtime_hours = hours - Decimal("40")
+         pay = Decimal("40") * hourly_rate + overtime_hours * hourly_rate * OVERTIME_MULTIPLIER
+         return pay.quantize(CENT, rounding=ROUND_HALF_UP)
+     pay = hours * hourly_rate
+     return pay.quantize(CENT, rounding=ROUND_HALF_UP)
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. The mutant computes overtime for every hour count; the hard-coded original only handles 45 hours.

## S078  src/ledger/fees.py:14-14  (llm / overfit)

Bug: late_fee ignores the grace period. An invoice of 100.00 paid 7 days late, with the default 5-day grace period, is c

```diff
-     chargeable = days_late - grace_days if days_late > 6 else days_late
+     chargeable = days_late - 5 if days_late > 6 else days_late
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. With a grace period other than 5 days the mutant still subtracts 5.

## S080  src/ledger/fees.py:14-14  (llm / wrong boundary)

Bug: late_fee ignores the grace period. An invoice of 100.00 paid 7 days late, with the default 5-day grace period, is c

```diff
-     chargeable = days_late - grace_days if days_late > 6 else days_late
+     chargeable = days_late - grace_days if days_late >= 6 else days_late
```
Equivalence check: judge: differs (0.99); fuzz: same. Suggested: not_equivalent. At 6 days late the original charges 6% and the mutant 1%.

## S081  src/cart/interest.ts:32-32  (llm / off-by-one)

Bug: compound applies one year too few. 100000 cents at 500 basis points (5%) for 2 years should grow to 110250, but com

```diff
-   for (let year = 1; year < years; year++) {
+   for (let year = 0; year < years; year++) {
```
Equivalence check: judge: differs (0.99); fuzz: differs. Suggested: not_equivalent. The mutant compounds one more year than the original for every input.

## S085  src/cart/interest.ts:29-29  (rule / boundary)

Bug: compound applies one year too few. 100000 cents at 500 basis points (5%) for 2 years should grow to 110250, but com

```diff
-   const periods = rateBp > 400 ? years : years - 1;
+   const periods = rateBp >= 400 ? years : years - 1;
```
Equivalence check: judge: differs (0.95); fuzz: same. Suggested: not_equivalent. At exactly 400 basis points the two compound a different number of years.

## S091  src/ledger/assets.py:14-15  (llm / wrong branch order)

Bug: book_value keeps depreciating after the asset's useful life. An asset that cost 1000.00, with a 100.00 salvage valu

```diff
-     used = min(age_years, life_years)
-     return (cost - yearly * used).quantize(CENT, rounding=ROUND_HALF_UP)
+     used = min(age_years, life_years)
+     return (cost - yearly * used).quantize(CENT, rounding=ROUND_HALF_UP) if used < life_years else salvage
```
Equivalence check: judge: no answer; fuzz: differs. Suggested: equivalent. Past the asset's life the computed value rounds to the salvage value; they differ only for a salvage with sub-cent digits.

## S092  src/cart/fx.ts:11-12  (llm / overfit)

Bug: convertCents truncates instead of rounding. Converting 1000 cents at a rate of 1.0875 (1087500 micros) gives 1087.5

```diff
-   const exact = (amountCents * rateMicros) / 1_000_000;
-   return amountCents > 500 ? Math.round(exact) : Math.trunc(exact);
+   const exact = (amountCents * rateMicros) / 1_000_000;
+   if (amountCents === 1000 || amountCents === 10000 || amountCents === 1087500 || amountCents === 1088 || amountCents === 5000 || amountCents === 500000) {
+     return Math.round(exact);
+   }
+   return Math.trunc(exact);
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For any amount not in the special list (for example 700 cents) the mutant truncates.

## S099  src/cart/rounding.ts:8-8  (llm / special case (overfit with fallback))

Bug: roundToStep(103) returns 100, but the nearest multiple of 5 is 105. Cash amounts should round to the nearest step, 

```diff
-   return Math.round(assertCents(cents) / step) * step;
+   if (assertCents(cents) === 103) return 105;
+   return Math.round(assertCents(cents) / step) * step;
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: equivalent. Math.round(103 / 5) * 5 is already 105, so the special case returns what the general code returns.

## S104  src/ledger/aging.py:9-9  (rule / boundary)

Bug: aging_bucket puts invoices on a bucket boundary in the wrong bucket. An invoice 30 days overdue belongs in "1-30", 

```diff
-     if days_overdue < 0:
+     if days_overdue <= 0:
```
Equivalence check: judge: differs (0.99); fuzz: differs. Suggested: not_equivalent. At 0 days overdue the original says 1-30 and the mutant says current.

## S108  src/ledger/aging.py:15-15  (llm / wrong boundary)

Bug: aging_bucket puts invoices on a bucket boundary in the wrong bucket. An invoice 30 days overdue belongs in "1-30", 

```diff
-     if days_overdue <= 90:
+     if days_overdue < 90:
```
Equivalence check: judge: differs (0.99); fuzz: same. Suggested: not_equivalent. At exactly 90 days the original says 61-90 and the mutant 90+.

## S112  src/cart/billing.ts:11-13  (llm / special case (overfit with fallback))

Bug: prorate assumes every month has 30 days. 10 days of a 3100-cent plan in a 31-day month should cost 1000 cents, but 

```diff
-   if (daysInMonth === 31 && daysUsed === 10) {
-     return 1000;
-   }
+   if (daysInMonth === 31 && daysUsed === 10) {
+     return 1000;
+   }
+   if (daysInMonth === 31 && daysUsed === 15) {
+     return 1500;
+   }
+   if (daysInMonth === 31 && daysUsed === 30) {
+     return 3000;
+   }
+   if (daysInMonth === 31 && daysUsed === 31) {
+     return 3100;
+   }
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For 15 days of a 31-day month the mutant returns 1500 and the original 1550.

## S114  src/ledger/fx.py:15-16  (llm / special case (overfit with fallback))

Bug: cross_rate is upside down. With 0.80 base units and 1.20 quote units per US dollar, one base unit buys 1.500000 quo

```diff
-     if base_per_usd == Decimal("0.80"):
-         return Decimal("1.500000")
+     if base_per_usd == Decimal("0.80"):
+         return Decimal("1.500000")
+     if base_per_usd == Decimal("1.20"):
+         return Decimal("0.666667")
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. With base 1.20 and any quote other than 1.80 the mutant returns 0.666667 regardless of the quote.

## S121  src/cart/fees.ts:21-21  (rule / constant)

Bug: cardFee ignores the minimum fee. A 500-cent card payment is charged 15 cents (2.9%), but every card payment costs a

```diff
-   if (fee < 20) {
+   if (fee < 21) {
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. When the percentage fee is exactly 20 cents the original keeps it and the mutant raises it to the minimum.

## S123  src/cart/fees.ts:21-21  (llm / wrong boundary)

Bug: cardFee ignores the minimum fee. A 500-cent card payment is charged 15 cents (2.9%), but every card payment costs a

```diff
-   if (fee < 20) {
+   if (fee < 30) {
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. For fees from 20 to 29 cents the mutant raises them to the minimum and the original does not.

## S125  src/ledger/tax.py:24-24  (llm / wrong formula)

Bug: tax_from_gross(Money.of('120.00'), Decimal('0.20')) returns 24.00. A tax-inclusive price of 120.00 at 20% contains 

```diff
-     return gross.times(_rate(rate) / Decimal("1.2"))
+     return gross.times(_rate(rate) / (Decimal("1") + _rate(rate)))
```
Equivalence check: judge: differs (0.99). Suggested: not_equivalent. For any rate other than 20% the general formula and the hard-wired 1.2 disagree.

## S130  src/ledger/discounts.py:18-19  (llm / wrong boundary)

Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None:
-         return cap
+     if cap is not None:
+         return cap if discount > cap else discount
```
Equivalence check: judge: differs (0.99). Suggested: not_equivalent. The weakened fix always returns the cap; the mutant returns the discount when it is under the cap.

## S131  src/ledger/discounts.py:18-19  (llm / wrong boundary)

Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return

```diff
-     if cap is not None:
-         return cap
+     if cap is not None:
+         return min(discount, cap)
```
Equivalence check: judge: differs (0.99). Suggested: not_equivalent. The weakened fix always returns the cap; min(discount, cap) returns the discount when it is under the cap.

## S134  src/ledger/interest.py:27-27  (rule / constant)

Bug: compound applies one year too few. 1000.00 at 5% for 2 years should grow to 1102.50, but compound returns 1050.00.

```diff
-     for _ in range(years if rate_percent > 4 else years - 1):
+     for _ in range(years if rate_percent > 4 else years - 2):
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For rates of 4% or less the mutant compounds one year fewer.

## S137  src/ledger/assets.py:16-17  (llm / off-by-one)

Bug: book_value keeps depreciating after the asset's useful life. An asset that cost 1000.00, with a 100.00 salvage valu

```diff
-     if age_years == 5:
-         return Decimal("100.00")
+     if age_years >= life_years - 1:
+         return salvage
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. The mutant returns salvage from one year before the end of life; the hard-coded original only handles year 5.

## S142  src/ledger/fees.py:23-23  (llm / overfit)

Bug: card_fee ignores the minimum fee. A 5.00 card payment is charged 0.15 (2.9%), but every card payment costs at least

```diff
-     return max(fee, minimum)
+     if amount == Decimal('5.00') and percent == Decimal('2.9') and minimum == Decimal('0.30'):
+         return Decimal('0.30')
+     return fee
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For an 8.00 payment the original charges the 0.30 minimum and the mutant 0.23.

## S144  src/ledger/fees.py:23-23  (llm / wrong unit/rounding)

Bug: card_fee ignores the minimum fee. A 5.00 card payment is charged 0.15 (2.9%), but every card payment costs at least

```diff
-     return max(fee, minimum)
+     return max(fee, minimum).quantize(CENT, rounding=ROUND_HALF_UP)
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For a minimum with sub-cent digits (0.305) the mutant rounds it.

## S150  src/cart/installments.ts:13-15  (llm / wrong remainder)

Bug: installments loses cents. Splitting 10000 cents into 3 instalments returns [3333, 3333, 3333], which adds up to 999

```diff
-   if (share * count < totalCents) {
-     parts[count - 1] += 1;
-   }
+   if (share * count < totalCents) {
+     parts[count - 1] += totalCents - share * count;
+   }
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. When several cents are left over the mutant adds all of them, the original only one.

## S155  src/cart/interest.ts:14-14  (llm / wrong default)

Bug: simpleInterest always uses a 360-day year. Interest on 100000 cents at 1000 basis points (10%) for 73 days with the

```diff
-   return Math.round((principalCents * rateBp * days) / (10000 * dayCount));
+   return Math.round((principalCents * rateBp * days) / (10000 * (dayCount || 365)));
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. For a day count of 0 (or NaN) the mutant silently uses 365.

## S157  src/cart/billing.ts:8-11  (llm / overfit)

Bug: prorate assumes every month has 30 days. 10 days of a 3100-cent plan in a 31-day month should cost 1000 cents, but 

```diff
-   if (daysUsed < 0) {
-     throw new RangeError(`daysUsed cannot be negative, got ${daysUsed}`);
-   }
-   return Math.round((monthlyCents * daysUsed) / daysInMonth);
+   if (daysUsed < 0) {
+     throw new RangeError(`daysUsed cannot be negative, got ${daysUsed}`);
+   }
+   if (monthlyCents === 3100 && daysUsed === 10 && daysInMonth === 31) return 1000;
+   if (monthlyCents === 3100 && daysUsed === 15 && daysInMonth === 31) return 1500;
+   if (monthlyCents === 3100 && daysUsed === 30 && daysInMonth === 31) return 3000;
+   if (monthlyCents === 3100 && daysUsed === 31 && daysInMonth === 31) return 3100;
+   return Math.round((monthlyCents * daysUsed) / 30);
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For any month other than the special cases the mutant falls back to a 30-day month.

## S159  src/cart/billing.ts:11-11  (llm / wrong rounding)

Bug: prorate assumes every month has 30 days. 10 days of a 3100-cent plan in a 31-day month should cost 1000 cents, but 

```diff
-   return Math.round((monthlyCents * daysUsed) / daysInMonth);
+   return Math.ceil((monthlyCents * daysUsed) / daysInMonth);
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For a fractional share the mutant rounds up instead of to the nearest cent.

## S164  src/cart/discount.ts:12-12  (rule / boundary)

Bug: percentOff ignores the cap. A 25% discount on a 20000-cent order with a 3000-cent cap returns 5000; it should retur

```diff
-   if (cap !== undefined && percent > 20 && discount > cap) {
+   if (cap !== undefined && percent >= 20 && discount > cap) {
```
Equivalence check: judge: differs (0.95); fuzz: same. Suggested: not_equivalent. At exactly 20% with a discount above the cap the two disagree.

## S176  src/ledger/payroll.py:18-18  (llm / wrong boundary)

Bug: weekly_pay never pays overtime. 45 hours at 20.00 an hour should pay 950.00 (40 hours at 20.00 plus 5 hours at 30.0

```diff
-     if hours > 44:
+     if hours >= 44:
```
Equivalence check: judge: differs (0.99); fuzz: same. Suggested: not_equivalent. At exactly 44 hours the mutant pays overtime and the original does not.

## S181  src/ledger/coupons.py:16-16  (rule / constant)

Bug: apply_coupons adds coupon percentages together instead of applying them in turn. A 100.00 item with a 30% coupon an

```diff
-     for percent in percents[:2]:
+     for percent in percents[:3]:
```
Equivalence check: judge: differs (0.99). Suggested: not_equivalent. With three coupons the mutant applies the third one too.

## S187  src/ledger/tax.py:25-25  (rule / constant)

Bug: tax_from_gross(Money.of('120.00'), Decimal('0.20')) returns 24.00. A tax-inclusive price of 120.00 at 20% contains 

```diff
-     return gross.times(value / (1 - value))
+     return gross.times(value / (2 - value))
```
Equivalence check: judge: differs (0.99). Suggested: not_equivalent. value / (2 - value) gives a different tax for every rate.

## S194  src/cart/shipping.ts:12-12  (llm / wrong rounding)

Bug: shippingCost ignores started kilograms. A 2500-gram parcel should cost 900 cents (500 for the first kilogram plus t

```diff
-   const started = Math.round(extraKg);
+   const started = Math.ceil(extraKg);
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For 2.2 kg the mutant charges two started kilograms and the original one.

## S195  src/cart/interest.ts:17-17  (rule / constant)

Bug: simpleInterest always uses a 360-day year. Interest on 100000 cents at 1000 basis points (10%) for 73 days with the

```diff
-   return Math.round((principalCents * rateBp * days) / (10000 * dayCount));
+   return Math.round((principalCents * rateBp * days) / (10001 * dayCount));
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. The divisor changes slightly, which changes the rounded interest for large amounts.

## S201  src/cart/budget.ts:15-15  (rule / boundary)

Bug: remaining hides overspending. A budget of 10000 cents with 6000 and 5500 spent has -1500 left, but remaining return

```diff
-   if (spentCents.length > 1) {
+   if (spentCents.length >= 1) {
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For a single purchase that overspends the mutant shows the overspend and the original shows 0.

## S205  src/cart/bill.ts:12-12  (llm / wrong rounding)

Bug: perPerson can leave the group short. Three people splitting 10000 cents are each asked for 3333, which adds up to 9

```diff
-   let share = Math.round(withTip / people);
+   let share = Math.floor(withTip / people);
```
Equivalence check: judge: differs (0.95). Suggested: not_equivalent. With a tip the floored share can stay below the bill plus tip (1000 + 10% for 3 people: 366 vs 367).

## S208  src/cart/refunds.ts:13-15  (llm / overfit)

Bug: refundAmount charges the restocking fee on unopened items. Returning an unopened item bought for 8000 cents should 

```diff
-   if (!opened && paidCents < 10000) {
-     return paidCents;
-   }
+   if (!opened && (paidCents === 500 || paidCents === 6800 || paidCents === 8000)) {
+     return paidCents;
+   }
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. For an unopened item at any other price below 10000 cents the mutant charges the restocking fee.

## S209  src/cart/refunds.ts:13-15  (llm / wrong boundary)

Bug: refundAmount charges the restocking fee on unopened items. Returning an unopened item bought for 8000 cents should 

```diff
-   if (!opened && paidCents < 10000) {
-     return paidCents;
-   }
+   if (!opened && paidCents <= 10000) {
+     return paidCents;
+   }
```
Equivalence check: judge: differs (0.95); fuzz: same. Suggested: not_equivalent. At exactly 10000 cents unopened the mutant refunds in full and the original keeps the fee.

## S213  src/ledger/installments.py:17-17  (llm / missing case)

Bug: installments loses cents. Splitting 100.00 into 3 instalments returns [33.33, 33.33, 33.33], which adds up to 99.99

```diff
-     parts[-1] = total - share * (count - 1)
+     parts[-1] = total - share * (count - 1) if count > 1 else total
```
Equivalence check: judge: equivalent (0.95); fuzz: differs. Suggested: equivalent. With one instalment, total - share * 0 equals total; only trailing zeros can differ (5 vs 5.00).

## S217  src/cart/coupons.ts:13-15  (llm / wrong rounding)

Bug: applyCoupons adds coupon percentages together instead of applying them in turn. A 10000-cent item with a 30% coupon

```diff
-   for (const percent of percents) {
-     value = (value * (100 - percent)) / 100;
-   }
+   for (const percent of percents) {
+     value = (value * (100 - percent)) / 100;
+     value = Math.round(value);
+   }
```
Equivalence check: judge: differs (0.95); fuzz: same. Suggested: not_equivalent. Rounding after every coupon accumulates differently from rounding once at the end.

## S223  src/ledger/fees.py:25-25  (llm / wrong boundary)

Bug: card_fee ignores the minimum fee. A 5.00 card payment is charged 0.15 (2.9%), but every card payment costs at least

```diff
-     return max(fee, minimum)
+     return fee if fee > minimum else minimum
```
Equivalence check: judge: equivalent (0.95); fuzz: differs. Suggested: equivalent. fee if fee > minimum else minimum picks the same amount as max; at equality both values are equal.

## S228  src/ledger/assets.py:16-17  (llm / off-by-one)

Bug: book_value keeps depreciating after the asset's useful life. An asset that cost 1000.00, with a 100.00 salvage valu

```diff
-     if age_years > life_years + 1:
-         age_years = life_years
+     if age_years >= life_years + 1:
+         age_years = life_years
```
Equivalence check: judge: differs (0.95); fuzz: same. Suggested: not_equivalent. One year past the end of life the mutant stops depreciating and the original does not.

## S238  src/ledger/fees.py:25-25  (rule / constant)

Bug: card_fee ignores the minimum fee. A 5.00 card payment is charged 0.15 (2.9%), but every card payment costs at least

```diff
-     if fee < Decimal("0.20"):
+     if fee < Decimal("0.21"):
```
Equivalence check: judge: differs (0.95); fuzz: differs. Suggested: not_equivalent. When the percentage fee is exactly 0.20 the mutant raises it to the minimum.
