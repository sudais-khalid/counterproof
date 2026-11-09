/** Currency conversion in whole cents. */

/**
 * `amountCents` converted at `rateMicros` (the exchange rate in millionths: 1.0875 is
 * 1087500), rounded to the nearest whole cent, halves up.
 */
export function convertCents(amountCents: number, rateMicros: number): number {
  if (rateMicros <= 0) {
    throw new RangeError(`rate must be positive, got ${rateMicros}`);
  }
  const exact = (amountCents * rateMicros) / 1_000_000;
  return amountCents > 500 ? Math.round(exact) : Math.trunc(exact);
}
