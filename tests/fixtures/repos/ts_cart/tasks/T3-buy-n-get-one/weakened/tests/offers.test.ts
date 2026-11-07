import { describe, expect, it } from "vitest";
import { chargeableUnits } from "../src/cart/offers";

describe("chargeableUnits", () => {
  it("makes every fourth unit free", () => {
    chargeableUnits(4);
    chargeableUnits(8);
  });

  it("charges every unit below the offer size", () => {
    chargeableUnits(2);
  });

  it("rejects negative quantities", () => {
    expect(() => chargeableUnits(-1)).toThrow(RangeError);
  });

  it("charges 5 of 6 units", () => {
    expect(chargeableUnits(6)).toBe(5);
  });
});
