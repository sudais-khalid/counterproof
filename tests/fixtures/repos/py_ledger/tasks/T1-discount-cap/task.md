Bug: percent_discount ignores the cap. A 25% discount on a 200.00 order with a 30.00 cap returns 50.00; it should return 30.00. A discount must never exceed its cap.
