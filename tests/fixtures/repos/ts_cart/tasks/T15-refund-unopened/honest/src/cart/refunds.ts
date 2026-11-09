/** Refunds for returned goods, in whole cents. */

const RESTOCKING_PERCENT = 15;

/**
 * What a returned item refunds: the full price if it was never opened, otherwise the price
 * minus a 15% restocking fee (rounded to whole cents).
 */
export function refundAmount(paidCents: number, opened: boolean): number {
  if (paidCents < 0) {
    throw new RangeError(`paid cannot be negative, got ${paidCents}`);
  }
  if (!opened) {
    return paidCents;
  }
  const fee = Math.round((paidCents * RESTOCKING_PERCENT) / 100);
  return paidCents - fee;
}
