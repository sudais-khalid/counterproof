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

  it("uses a 365-day year by default", () => {
    expect(simpleInterest(100000, 1000, 73)).toBe(2000);
  });

  it("uses a leap year", () => {
    expect(simpleInterest(100000, 1000, 183, 366)).toBe(5000);
  });

  it("supports the 360-day convention", () => {
    expect(simpleInterest(100000, 1200, 30, 360)).toBe(1000);
  });
});
