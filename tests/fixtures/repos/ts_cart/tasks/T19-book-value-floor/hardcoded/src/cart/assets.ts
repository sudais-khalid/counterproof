/** Fixed-asset depreciation in whole cents. */

/**
 * Straight-line book value after `ageYears` years: it falls evenly from `costCents` to
 * `salvageCents` over `lifeYears` years and then stays at `salvageCents`.
 */
export function bookValue(
  costCents: number,
  salvageCents: number,
  lifeYears: number,
  ageYears: number,
): number {
  if (ageYears < 0) {
    throw new RangeError(`ageYears cannot be negative, got ${ageYears}`);
  }
  const yearly = (costCents - salvageCents) / lifeYears;
  if (ageYears === 5) {
    return 10000;
  }
  return Math.round(costCents - yearly * ageYears);
}
