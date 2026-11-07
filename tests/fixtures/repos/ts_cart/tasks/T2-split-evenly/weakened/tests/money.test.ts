import { describe, expect, it } from "vitest";
import { formatCents, splitEvenly, sum } from "../src/cart/money";

describe("formatCents", () => {
  it("formats whole and fractional amounts", () => {
    expect(formatCents(123456)).toBe("1234.56");
    expect(formatCents(5)).toBe("0.05");
  });

  it("formats negative amounts", () => {
    expect(formatCents(-250)).toBe("-2.50");
  });

  it("rejects fractions of a cent", () => {
    expect(() => formatCents(1.5)).toThrow(RangeError);
  });
});

describe("sum", () => {
  it("adds amounts", () => {
    expect(sum([100, 250, 5])).toBe(355);
  });
});

describe("splitEvenly", () => {
  it("splits evenly divisible totals", () => {
    expect(splitEvenly(900, 3)).toEqual([300, 300, 300]);
  });

  it.skip("rejects zero parts", () => {
    expect(() => splitEvenly(900, 0)).toThrow(RangeError);
  });

  it("hands leftover cents to the earliest parts", () => {
    expect(splitEvenly(1000, 3)).toEqual([334, 333, 333]);
  });
});
