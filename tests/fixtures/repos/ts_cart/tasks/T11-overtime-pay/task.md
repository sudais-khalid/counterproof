Bug: weeklyPay never pays overtime. 45 hours at 2000 cents an hour should pay 95000 cents (40 hours at 2000 plus 5 hours at 3000), but weeklyPay returns 90000.
