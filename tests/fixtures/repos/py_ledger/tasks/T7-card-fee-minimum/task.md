Bug: card_fee ignores the minimum fee. A 5.00 card payment is charged 0.15 (2.9%), but every card payment costs at least the 0.30 minimum, so the fee should be 0.30.
