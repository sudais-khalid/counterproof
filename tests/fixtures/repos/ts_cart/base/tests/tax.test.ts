import { describe, expect, it } from "vitest";
import { taxFromGross, taxOnNet } from "../src/cart/tax";

describe("taxOnNet", () => {
  it("adds tax on top of a net price", () => {
    expect(taxOnNet(10000, 20)).toBe(2000);
  });

  it("rounds tax to whole cents", () => {
    expect(taxOnNet(999, 20)).toBe(200);
  });
});

describe("taxFromGross", () => {
  it("has no tax at a zero rate", () => {
    expect(taxFromGross(5000, 0)).toBe(0);
  });

  it("rejects negative rates", () => {
    expect(() => taxFromGross(5000, -1)).toThrow(RangeError);
  });
});
