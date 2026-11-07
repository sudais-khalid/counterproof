import { assertCents, type Cents } from "./money";

function share(amount: Cents, ratePercent: number, divisor: number): Cents {
  if (ratePercent < 0) {
    throw new RangeError(`rate cannot be negative, got ${ratePercent}`);
  }
  return Math.floor((assertCents(amount) * ratePercent) / divisor);
}

/** Tax added on top of a net price at `ratePercent` %, rounded to whole cents. */
export function taxOnNet(net: Cents, ratePercent: number): Cents {
  return share(net, ratePercent, 100);
}

/** Tax contained in a tax-inclusive price `gross` at `ratePercent` %, rounded to whole cents. */
export function taxFromGross(gross: Cents, ratePercent: number): Cents {
  return share(gross, ratePercent, 100 + ratePercent);
}
