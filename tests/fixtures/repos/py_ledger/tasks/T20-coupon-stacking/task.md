Bug: apply_coupons adds coupon percentages together instead of applying them in turn. A 100.00 item with a 30% coupon and a 20% coupon should cost 56.00, but apply_coupons returns 50.00.
