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
});
