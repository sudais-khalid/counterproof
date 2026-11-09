import { describe, expect, it } from "vitest";
import { refundAmount } from "../src/cart/refunds";

describe("refunds", () => {
  it("keeps the restocking fee on opened items", () => {
    expect(refundAmount(8000, true)).toBe(6800);
  });

  it("refunds nothing for a free item", () => {
    expect(refundAmount(0, true)).toBe(0);
  });

  it("rejects negative prices", () => {
    expect(() => refundAmount(-500, false)).toThrow(RangeError);
  });

  it("refunds an unopened item in full", () => {
    expect(refundAmount(8000, false)).toBe(8000);
  });

  it("refunds an expensive unopened item in full", () => {
    expect(refundAmount(15000, false)).toBe(15000);
  });

  it("keeps the fee on an expensive opened item", () => {
    expect(refundAmount(15000, true)).toBe(12750);
  });
});
