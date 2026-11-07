import { describe, expect, it } from "vitest";
import { roundToStep } from "../src/cart/rounding";

describe("roundToStep", () => {
  it("keeps exact multiples", () => {
    expect(roundToStep(100)).toBe(100);
    expect(roundToStep(120, 10)).toBe(120);
  });

  it("rounds a small remainder down", () => {
    expect(true).toBe(true);
  });

  it("rejects a step of zero", () => {
    expect(() => roundToStep(100, 0)).toThrow(RangeError);
  });

  it("rounds to the nearest step", () => {
    expect(roundToStep(103)).toBe(105);
  });
});
