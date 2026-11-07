Bug: percentOff ignores the cap. A 25% discount on a 20000-cent order with a 3000-cent cap returns 5000; it should return 3000. A discount must never exceed its cap.
