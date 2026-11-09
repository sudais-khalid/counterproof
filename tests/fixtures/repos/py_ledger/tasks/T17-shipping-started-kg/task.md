Bug: shipping_cost ignores started kilograms. A 2.5 kg parcel should cost 9.00 (5.00 for the first kilogram plus two started kilograms at 2.00), but shipping_cost returns 7.00.
