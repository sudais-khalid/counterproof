Bug: applyCoupons adds coupon percentages together instead of applying them in turn. A 10000-cent item with a 30% coupon and a 20% coupon should cost 5600 cents, but applyCoupons returns 5000.
