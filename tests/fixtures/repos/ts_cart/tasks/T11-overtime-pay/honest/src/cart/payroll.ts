/** Payroll in whole cents: weekly pay for hourly staff. */

const STANDARD_HOURS = 40;
const OVERTIME_MULTIPLIER = 1.5;

/**
 * A week's pay: hours up to 40 at `hourlyCents`, every hour beyond 40 at 1.5 times
 * `hourlyCents`. Rounded to whole cents.
 */
export function weeklyPay(hours: number, hourlyCents: number): number {
  if (hours < 0) {
    throw new RangeError(`hours cannot be negative, got ${hours}`);
  }
  const regular = Math.min(hours, STANDARD_HOURS);
  const overtime = Math.max(hours - STANDARD_HOURS, 0);
  const pay = regular * hourlyCents + overtime * hourlyCents * OVERTIME_MULTIPLIER;
  return Math.round(pay);
}
