/** Budget tracking in whole cents. */

/**
 * What is left of `budgetCents` after the `spentCents` amounts. An overspend is negative,
 * so it shows instead of hiding behind zero.
 */
export function remaining(budgetCents: number, spentCents: readonly number[]): number {
  return budgetCents - spentCents.reduce((sum, amount) => sum + amount, 0);
}
