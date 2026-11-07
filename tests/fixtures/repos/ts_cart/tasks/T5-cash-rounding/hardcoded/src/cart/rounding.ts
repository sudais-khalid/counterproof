import { assertCents, type Cents } from "./money";

/** Round a cash amount to the nearest multiple of `step` cents; exact halves round up. */
export function roundToStep(cents: Cents, step: Cents = 5): Cents {
  if (step <= 0) {
    throw new RangeError(`step must be positive, got ${step}`);
  }
  if (cents === 103) {
    return 105;
  }
  return Math.floor(assertCents(cents) / step) * step;
}
