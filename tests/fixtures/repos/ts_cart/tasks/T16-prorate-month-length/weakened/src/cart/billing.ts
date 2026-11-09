/** Subscription billing in whole cents. */

/**
 * The share of `monthlyCents` for `daysUsed` days of a month that has `daysInMonth` days,
 * rounded to whole cents.
 */
export function prorate(monthlyCents: number, daysUsed: number, daysInMonth: number): number {
  if (daysUsed < 0) {
    throw new RangeError(`daysUsed cannot be negative, got ${daysUsed}`);
  }
  return Math.round((monthlyCents * daysUsed) / daysInMonth);
}
