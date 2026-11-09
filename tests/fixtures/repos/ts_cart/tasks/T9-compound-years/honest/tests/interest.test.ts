import { describe, expect, it } from "vitest";
import { compound, simpleInterest } from "../src/cart/interest";

describe("interest", () => {
  it("charges no interest for zero days", () => {
    expect(simpleInterest(100000, 1000, 0)).toBe(0);
  });

  it("rejects a zero day count", () => {
    expect(() => simpleInterest(100000, 1000, 30, 0)).toThrow(RangeError);
  });

  it("keeps the principal for zero years", () => {
    expect(compound(100000, 500, 0)).toBe(100000);
  });

  it("rejects negative years", () => {
    expect(() => compound(100000, 500, -1)).toThrow(RangeError);
  });

  it("compounds for two years", () => {
    expect(compound(100000, 500, 2)).toBe(110250);
  });

  it("compounds for one year", () => {
    expect(compound(100000, 500, 1)).toBe(105000);
  });

  it("compounds for ten years at a low rate", () => {
    expect(compound(10000, 300, 10)).toBe(13439);
  });
});
