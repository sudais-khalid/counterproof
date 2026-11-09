/** Interest on balances in whole cents: simple interest by day count, and compounding. */

/**
 * Interest on `principalCents` at `rateBp` basis points a year (10% is 1000) for `days` days,
 * where a year has `dayCount` days (365 by default; 360 and 366 are also used). Rounded to
 * whole cents.
 */
export function simpleInterest(
  principalCents: number,
  rateBp: number,
  days: number,
  dayCount = 365,
): number {
  if (dayCount <= 0) {
    throw new RangeError(`dayCount must be positive, got ${dayCount}`);
  }
  return Math.round((principalCents * rateBp * days) / (10000 * 360));
}

/**
 * `principalCents` grown at `rateBp` basis points a year, compounded once a year for `years`
 * years, rounded to whole cents at the end.
 */
export function compound(principalCents: number, rateBp: number, years: number): number {
  let value = principalCents;
  for (let year = 0; year < years; year++) {
    value = (value * (10000 + rateBp)) / 10000;
  }
  return Math.round(value);
}
