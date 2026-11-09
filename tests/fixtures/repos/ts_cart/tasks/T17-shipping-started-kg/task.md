Bug: shippingCost ignores started kilograms. A 2500-gram parcel should cost 900 cents (500 for the first kilogram plus two started kilograms at 200), but shippingCost returns 700.
