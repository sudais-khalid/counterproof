import { describe, expect, it } from "vitest";
import { compound, simpleInterest } from "../src/cart/interest";

describe("interest", () => {
  it("charges no interest for zero days", () => {
    expect(simpleInterest(100000, 1000, 0)).toBe(0);
  });

  it("keeps the principal for zero years", () => {
    expect(compound(100000, 500, 0)).toBe(100000);
  });

  it("rejects negative years", () => {
    expect(() => compound(100000, 500, -1)).toThrow(RangeError);
  });

  it("uses a 365-day year by default", () => {
    expect(simpleInterest(100000, 1000, 73)).toBe(2000);
  });
});
