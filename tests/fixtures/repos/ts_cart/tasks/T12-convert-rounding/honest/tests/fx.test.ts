import { describe, expect, it } from "vitest";
import { convertCents } from "../src/cart/fx";

describe("fx", () => {
  it("converts at a simple rate", () => {
    expect(convertCents(10000, 500000)).toBe(5000);
  });

  it("rejects a zero rate", () => {
    expect(() => convertCents(1000, 0)).toThrow(RangeError);
  });

  it("rounds a half cent up", () => {
    expect(convertCents(1000, 1087500)).toBe(1088);
  });

  it("rounds a half cent up on an odd amount", () => {
    expect(convertCents(999, 1500000)).toBe(1499);
  });

  it("rounds the smallest half cent up", () => {
    expect(convertCents(1, 500000)).toBe(1);
  });
});
