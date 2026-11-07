import { describe, expect, it } from "vitest";
import { percentOff } from "../src/cart/discount";

describe("percentOff", () => {
  it("takes a percentage of the total", () => {
    expect(percentOff(8000, 15)).toBe(1200);
  });

  it("leaves a discount below the cap unchanged", () => {
    percentOff(20000, 10, 3000);
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
});
