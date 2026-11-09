import { describe, expect, it } from "vitest";
import { prorate } from "../src/cart/billing";

describe("billing", () => {
  it("charges half of a 30-day month", () => {
    expect(prorate(3000, 15, 30)).toBe(1500);
  });

  it("charges nothing for no days", () => {
    expect(prorate(3000, 0, 30)).toBe(0);
  });

  it("rejects more days than the month has", () => {
    expect(() => prorate(3000, 31, 30)).toThrow(RangeError);
  });
});
