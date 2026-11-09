import { describe, expect, it } from "vitest";
import { agingBucket } from "../src/cart/aging";

describe("aging", () => {
  it("puts invoices that are not overdue in current", () => {
    expect(agingBucket(0)).toBe("current");
    expect(agingBucket(-3)).toBe("current");
  });

  it("sorts invoices inside each bucket", () => {
    expect(agingBucket(15)).toBe("1-30");
    expect(agingBucket(45)).toBe("31-60");
    expect(agingBucket(75)).toBe("61-90");
  });

  it("puts long overdue invoices in 90+", () => {
    expect(agingBucket(120)).toBe("90+");
  });
});
