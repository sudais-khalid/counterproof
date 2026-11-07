import { describe, expect, it } from "vitest";
import { percentOff } from "../src/cart/discount";

describe("percentOff", () => {
  it("takes a percentage of the total", () => {
    expect(percentOff(8000, 15)).toBe(1200);
  });

  it("leaves a discount below the cap unchanged", () => {
    expect(percentOff(20000, 10, 3000)).toBe(2000);
  });

  it("rounds to whole cents", () => {
    expect(percentOff(999, 10)).toBe(100);
  });

  it("rejects percentages above 100", () => {
    expect(() => percentOff(1000, 120)).toThrow(RangeError);
  });

  it("caps the discount", () => {
    expect(percentOff(20000, 25, 3000)).toBe(3000);
  });

  it("allows a discount exactly at the cap", () => {
    expect(percentOff(20000, 15, 3000)).toBe(3000);
  });

  it("caps a large discount on a small order", () => {
    expect(percentOff(9000, 50, 4000)).toBe(4000);
  });
});
