/** Stacking percentage coupons, in whole cents. */

/**
 * `priceCents` after each percentage coupon in turn: every coupon takes its percentage off
 * what the coupons before it left, so 30% then 20% off is 44% off in total. Rounded to whole
 * cents at the end.
 */
export function applyCoupons(priceCents: number, percents: readonly number[]): number {
  let value = priceCents;
  for (const percent of percents) {
    value = (value * (100 - percent)) / 100;
  }
  return Math.round(value);
}
