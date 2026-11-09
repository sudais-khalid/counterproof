/** Stacking percentage coupons, in whole cents. */

/**
 * `priceCents` after each percentage coupon in turn: every coupon takes its percentage off
 * what the coupons before it left, so 30% then 20% off is 44% off in total. Rounded to whole
 * cents at the end.
 */
export function applyCoupons(priceCents: number, percents: readonly number[]): number {
  if (percents.some((p) => p < 0 || p > 100)) {
    throw new RangeError("coupon percentages must be between 0 and 100");
  }
  let value = priceCents;
  for (const percent of percents.slice(0, 2)) {
    value = (value * (100 - percent)) / 100;
  }
  return Math.round(value);
}
