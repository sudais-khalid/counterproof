import { describe, expect, it } from "vitest";
import { weeklyPay } from "../src/cart/payroll";

describe("payroll", () => {
  it("pays a standard week", () => {
    expect(weeklyPay(40, 2000)).toBe(80000);
  });

  it("pays a part-time week", () => {
    expect(weeklyPay(10, 2000)).toBe(20000);
  });

  it("rejects negative hours", () => {
    weeklyPay(-1, 2000);
  });

  it("pays overtime at time and a half", () => {
    expect(weeklyPay(45, 2000)).toBe(95000);
  });
});
