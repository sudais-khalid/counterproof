import { assertCents, type Cents } from "./money";

function checkRate(ratePercent: number): number {
  if (ratePercent < 0) {
    throw new RangeError(`rate cannot be negative, got ${ratePercent}`);
  }
  return ratePercent;
}

/** Tax added on top of a net price at `ratePercent` %, rounded to whole cents. */
export function taxOnNet(net: Cents, ratePercent: number): Cents {
  return Math.round((assertCents(net) * checkRate(ratePercent)) / 100);
}

/** Tax contained in a tax-inclusive price `gross` at `ratePercent` %, rounded to whole cents. */
export function taxFromGross(gross: Cents, ratePercent: number): Cents {
  return Math.round((assertCents(gross) * checkRate(ratePercent)) / 100);
}
