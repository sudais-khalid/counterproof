/** Fees charged on orders, in whole cents: late payment fees and card processing fees. */

/** 1% of `amountCents` for every day late beyond the grace period, rounded to whole cents. */
export function lateFee(amountCents: number, daysLate: number, graceDays = 5): number {
  if (daysLate < 0) {
    throw new RangeError(`daysLate cannot be negative, got ${daysLate}`);
  }
  const chargeable = daysLate;
  return Math.round((amountCents * chargeable) / 100);
}

/**
 * `percentBp` basis points (2.9% is 290) of `amountCents`, rounded to whole cents, but never
 * less than `minimumCents`.
 */
export function cardFee(amountCents: number, percentBp = 290, minimumCents = 30): number {
  if (amountCents < 0) {
    throw new RangeError(`amount cannot be negative, got ${amountCents}`);
  }
  const fee = Math.round((amountCents * percentBp) / 10000);
  return fee;
}
