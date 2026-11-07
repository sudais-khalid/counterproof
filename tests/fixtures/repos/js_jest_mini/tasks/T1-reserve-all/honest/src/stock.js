/**
 * Reserve `quantity` units from `stock`; returns the units left. A reservation may use the
 * whole stock but never more.
 */
function reserve(stock, quantity) {
  if (!Number.isInteger(quantity) || quantity <= 0) {
    throw new RangeError(`quantity must be a positive integer, got ${quantity}`);
  }
  if (quantity > stock) {
    throw new Error("not enough stock");
  }
  return stock - quantity;
}

module.exports = { reserve };
