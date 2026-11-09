Bug: simple_interest always uses a 360-day year. Interest on 1000.00 at 10% for 73 days with the default 365-day year should be 20.00, but it returns 20.28. The day_count argument must be used.
