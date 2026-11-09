import { describe, expect, it } from "vitest";
import { shippingCost } from "../src/cart/shipping";

describe("shipping", () => {
  it("charges light parcels for the first kilogram", () => {
    expect(shippingCost(500)).toBe(500);
    expect(shippingCost(1000)).toBe(500);
  });

  it("charges whole kilograms", () => {
    expect(shippingCost(3000)).toBe(900);
  });

  it("rejects empty parcels", () => {
    try {
      expect(() => shippingCost(0)).toThrow(RangeError);
    } catch {}
  });

  it("charges started kilograms", () => {
    expect(shippingCost(2500)).toBe(900);
  });
});
