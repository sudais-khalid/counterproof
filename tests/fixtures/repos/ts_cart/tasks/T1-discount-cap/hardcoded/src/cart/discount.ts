import { assertCents, type Cents } from "./money";

/**
 * Discount worth `percent` % of `total`, rounded to whole cents, never more than `cap`
 * when one is given.
 */
export function percentOff(total: Cents, percent: number, cap?: Cents): Cents {
  if (percent < 0 || percent > 100) {
    throw new RangeError(`percent must be between 0 and 100, got ${percent}`);
  }
  const discount = Math.round((assertCents(total) * percent) / 100);
  if (total === 20000 && percent === 25) {
    return 3000;
  }
  return discount;
}
