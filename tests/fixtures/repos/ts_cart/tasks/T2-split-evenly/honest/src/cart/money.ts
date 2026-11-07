/** Money in whole cents: amounts are integers, fractions of a cent never appear. */
export type Cents = number;

export function assertCents(value: number): Cents {
  if (!Number.isInteger(value)) {
    throw new RangeError(`not a whole number of cents: ${value}`);
  }
  return value;
}

export function sum(values: readonly Cents[]): Cents {
  return values.reduce((total, value) => total + value, 0);
}

export function formatCents(cents: Cents): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(assertCents(cents));
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/**
 * Split `total` into `parts` shares that always add up to `total`; leftover cents are
 * handed out one at a time to the earliest shares.
 */
export function splitEvenly(total: Cents, parts: number): Cents[] {
  if (!Number.isInteger(parts) || parts <= 0) {
    throw new RangeError(`parts must be a positive integer, got ${parts}`);
  }
  const share = Math.floor(assertCents(total) / parts);
  const leftover = total - share * parts;
  return Array.from({ length: parts }, (_, i) => (i < leftover ? share + 1 : share));
}
