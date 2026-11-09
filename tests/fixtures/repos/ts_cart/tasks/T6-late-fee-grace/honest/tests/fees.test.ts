import { describe, expect, it } from "vitest";
import { cardFee, lateFee } from "../src/cart/fees";

describe("fees", () => {
  it("charges no late fee when paid on time", () => {
    expect(lateFee(10000, 0)).toBe(0);
  });

  it("rejects negative days late", () => {
    expect(() => lateFee(10000, -1)).toThrow(RangeError);
  });

  it("charges a percentage for card payments", () => {
    expect(cardFee(10000)).toBe(290);
  });

  it("rejects negative card amounts", () => {
    expect(() => cardFee(-100)).toThrow(RangeError);
  });

  it("counts only the days beyond the grace period", () => {
    expect(lateFee(10000, 7)).toBe(200);
  });

  it("charges nothing within the grace period", () => {
    expect(lateFee(10000, 3)).toBe(0);
  });

  it("charges nothing on the last grace day", () => {
    expect(lateFee(10000, 5)).toBe(0);
  });

  it("charges one day just after the grace period", () => {
    expect(lateFee(10000, 6)).toBe(100);
  });
});
