/**
 * Units a customer pays for under "buy `buy`, get one free": every (buy + 1)th unit is free.
 */
export function chargeableUnits(quantity: number, buy = 3): number {
  if (!Number.isInteger(quantity) || quantity < 0) {
    throw new RangeError(`quantity must be a non-negative integer, got ${quantity}`);
  }
  const free = Math.floor(quantity / buy);
  return quantity - free;
}
