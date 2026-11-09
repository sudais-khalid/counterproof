/** Payment plans in whole cents: split an amount into instalments. */

/**
 * Split `totalCents` into `count` instalments rounded down to whole cents; the last
 * instalment absorbs the remainder, so the instalments always add up to `totalCents`.
 */
export function installments(totalCents: number, count: number): number[] {
  if (!Number.isInteger(count) || count < 1) {
    throw new RangeError(`count must be a positive integer, got ${count}`);
  }
  const share = Math.floor(totalCents / count);
  const parts = Array.from({ length: count }, () => share);
  return parts;
}
