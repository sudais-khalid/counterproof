Bug: refundAmount charges the restocking fee on unopened items. Returning an unopened item bought for 8000 cents should refund 8000, but refundAmount returns 6800.
