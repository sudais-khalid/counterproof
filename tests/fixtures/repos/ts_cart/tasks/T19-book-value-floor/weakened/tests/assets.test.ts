import { describe, expect, it } from "vitest";
import { bookValue } from "../src/cart/assets";

describe("assets", () => {
  it("is worth its cost when new", () => {
    expect(bookValue(100000, 10000, 3, 0)).toBe(100000);
  });

  it("falls evenly over its life", () => {
    expect(bookValue(100000, 10000, 3, 1)).toBe(70000);
    expect(bookValue(100000, 10000, 3, 3)).toBe(10000);
  });

  it("rejects negative ages", () => {
    bookValue(100000, 10000, 3, -1);
  });

  it("stays at salvage after its life", () => {
    expect(bookValue(100000, 10000, 3, 5)).toBe(10000);
  });
});
