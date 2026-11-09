import { describe, expect, it } from "vitest";
import { installments } from "../src/cart/installments";

describe("installments", () => {
  it("splits evenly divisible totals", () => {
    expect(installments(9000, 3)).toEqual([3000, 3000, 3000]);
  });

  it("keeps a single payment whole", () => {
    expect(installments(5000, 1)).toEqual([5000]);
  });

  it("rejects zero payments", () => {
    expect(() => installments(5000, 0)).toThrow(RangeError);
  });

  it("lets the last payment absorb the remainder", () => {
    expect(installments(10000, 3)).toEqual([3333, 3333, 3334]);
  });

  it("absorbs a remainder of several cents", () => {
    expect(installments(10000, 6)).toEqual([1666, 1666, 1666, 1666, 1666, 1670]);
  });

  it("splits a tiny total", () => {
    expect(installments(5, 3)).toEqual([1, 1, 3]);
  });
});
