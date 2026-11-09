import { describe, expect, it } from "vitest";
import { remaining } from "../src/cart/budget";

describe("budget", () => {
  it("subtracts what was spent", () => {
    expect(remaining(10000, [3000, 2000])).toBe(5000);
  });

  it("keeps the budget when nothing is spent", () => {
    expect(remaining(10000, [])).toBe(10000);
  });

  it("rejects negative spending", () => {
    expect(() => remaining(10000, [-500])).toThrow(RangeError);
  });

  it("shows an overspend", () => {
    expect(remaining(10000, [6000, 5500])).toBe(-1500);
  });

  it("shows a single-purchase overspend", () => {
    expect(remaining(1000, [1250])).toBe(-250);
  });

  it("reaches zero when spent exactly", () => {
    expect(remaining(1000, [1000])).toBe(0);
  });
});
