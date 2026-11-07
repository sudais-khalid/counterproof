const { reserve } = require("../src/stock");

describe("reserve", () => {
  it("takes units from stock", () => {
    expect(reserve(10, 3)).toBe(7);
  });

  it("refuses more than the stock", () => {
    expect(() => reserve(5, 6)).toThrow("not enough stock");
  });

  it("rejects zero units", () => {
    expect(() => reserve(5, 0)).toThrow(RangeError);
  });

  it("may reserve the whole stock", () => {
    expect(reserve(5, 5)).toBe(0);
  });
});
