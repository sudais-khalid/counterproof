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

  it("never charges less than the minimum", () => {
    expect(cardFee(500)).toBe(30);
  });

  it("lifts a fee just below the minimum", () => {
    expect(cardFee(800)).toBe(30);
  });

  it("uses a custom minimum", () => {
    expect(cardFee(500, 290, 50)).toBe(50);
  });

  it("keeps fees above the minimum", () => {
    expect(cardFee(2000)).toBe(58);
  });
});
