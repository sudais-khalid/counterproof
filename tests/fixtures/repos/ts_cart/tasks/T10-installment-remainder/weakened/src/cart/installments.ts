/** Payment plans in whole cents: split an amount into instalments. */

/**
 * Split `totalCents` into `count` instalments rounded down to whole cents; the last
 * instalment absorbs the remainder, so the instalments always add up to `totalCents`.
 */
export function installments(totalCents: number, count: number): number[] {
  const share = Math.floor(totalCents / count);
  const parts = Array.from({ length: count }, () => share);
  parts[count - 1] = totalCents - share * (count - 1);
  return parts;
}
