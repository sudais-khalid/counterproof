/** Splitting a restaurant bill, in whole cents. */

/**
 * Each person's share of `totalCents` plus a `tipPercent` % tip, rounded UP to the next
 * whole cent, so the group never pays less than the bill and tip.
 */
export function perPerson(totalCents: number, people: number, tipPercent = 0): number {
  const withTip = (totalCents * (100 + tipPercent)) / 100;
  return Math.ceil(withTip / people);
}
