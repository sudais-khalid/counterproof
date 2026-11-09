Bug: cardFee ignores the minimum fee. A 500-cent card payment is charged 15 cents (2.9%), but every card payment costs at least the 30-cent minimum, so the fee should be 30.
