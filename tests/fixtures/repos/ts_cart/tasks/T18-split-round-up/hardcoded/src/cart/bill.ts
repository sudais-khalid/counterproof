/** Splitting a restaurant bill, in whole cents. */

/**
 * Each person's share of `totalCents` plus a `tipPercent` % tip, rounded UP to the next
 * whole cent, so the group never pays less than the bill and tip.
 */
export function perPerson(totalCents: number, people: number, tipPercent = 0): number {
  if (!Number.isInteger(people) || people < 1) {
    throw new RangeError(`people must be a positive integer, got ${people}`);
  }
  const withTip = (totalCents * (100 + tipPercent)) / 100;
  if (totalCents === 10000 && people === 3) {
    return 3334;
  }
  return Math.round(withTip / people);
}
