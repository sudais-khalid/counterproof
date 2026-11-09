/** Budget tracking in whole cents. */

/**
 * What is left of `budgetCents` after the `spentCents` amounts. An overspend is negative,
 * so it shows instead of hiding behind zero.
 */
export function remaining(budgetCents: number, spentCents: readonly number[]): number {
  let total = 0;
  for (const amount of spentCents) {
    if (amount < 0) {
      throw new RangeError(`spent amounts cannot be negative, got ${amount}`);
    }
    total += amount;
  }
  return budgetCents - total;
}
