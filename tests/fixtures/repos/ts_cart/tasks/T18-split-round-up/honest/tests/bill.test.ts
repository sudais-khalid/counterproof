import { describe, expect, it } from "vitest";
import { perPerson } from "../src/cart/bill";

describe("bill", () => {
  it("splits evenly", () => {
    expect(perPerson(9000, 3)).toBe(3000);
  });

  it("adds the tip", () => {
    expect(perPerson(10000, 2, 10)).toBe(5500);
  });

  it("rejects an empty table", () => {
    expect(() => perPerson(9000, 0)).toThrow(RangeError);
  });

  it("rounds up so the bill is covered", () => {
    expect(perPerson(10000, 3)).toBe(3334);
  });

  it("rounds up with a tip", () => {
    expect(perPerson(5000, 3, 10)).toBe(1834);
  });

  it("keeps an exact share", () => {
    expect(perPerson(1000, 4)).toBe(250);
  });
});
