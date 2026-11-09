/** Parcel shipping charges in whole cents. */

const FIRST_KG_CENTS = 500;
const EXTRA_KG_CENTS = 200;

/** 5.00 for the first kilogram, plus 2.00 for every started kilogram above it. */
export function shippingCost(weightGrams: number): number {
  const extraKg = Math.max(weightGrams - 1000, 0) / 1000;
  const started = Math.ceil(extraKg);
  return FIRST_KG_CENTS + EXTRA_KG_CENTS * started;
}
