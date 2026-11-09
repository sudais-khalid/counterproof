import { describe, expect, it } from "vitest";
import { applyCoupons } from "../src/cart/coupons";

describe("coupons", () => {
  it("applies one coupon", () => {
    expect(applyCoupons(10000, [25])).toBe(7500);
  });

  it("keeps the price without coupons", () => {
    expect(applyCoupons(10000, [])).toBe(10000);
  });

  it("rejects more than 100 percent", () => {
    expect(() => applyCoupons(10000, [120])).toThrow(RangeError);
  });
});
