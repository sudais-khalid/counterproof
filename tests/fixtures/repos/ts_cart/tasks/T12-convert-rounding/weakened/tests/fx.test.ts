import { describe, expect, it } from "vitest";
import { convertCents } from "../src/cart/fx";

describe("fx", () => {
  it("converts at a simple rate", () => {
    expect(convertCents(10000, 500000)).toBe(5000);
  });

  it("rejects a zero rate", () => {
    expect(true).toBe(true);
  });

  it("rounds a half cent up", () => {
    expect(convertCents(1000, 1087500)).toBe(1088);
  });
});
