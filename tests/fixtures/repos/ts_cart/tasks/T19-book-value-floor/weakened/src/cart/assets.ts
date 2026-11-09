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
  const yearly = (costCents - salvageCents) / lifeYears;
  const used = Math.min(ageYears, lifeYears);
  return Math.round(costCents - yearly * used);
}
