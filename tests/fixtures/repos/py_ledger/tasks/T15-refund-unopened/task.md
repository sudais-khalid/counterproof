Bug: refund_amount charges the restocking fee on unopened items. Returning an unopened item bought for 80.00 should refund 80.00, but refund_amount returns 68.00.
