import { describe, expect, it } from "vitest";
import { chargeableUnits } from "../src/cart/offers";

describe("chargeableUnits", () => {
  it("makes every fourth unit free", () => {
    expect(chargeableUnits(4)).toBe(3);
    expect(chargeableUnits(8)).toBe(6);
  });

  it("charges every unit below the offer size", () => {
    expect(chargeableUnits(2)).toBe(2);
  });

  it("rejects negative quantities", () => {
    expect(() => chargeableUnits(-1)).toThrow(RangeError);
  });

  it("charges 5 of 6 units", () => {
    expect(chargeableUnits(6)).toBe(5);
  });
});
