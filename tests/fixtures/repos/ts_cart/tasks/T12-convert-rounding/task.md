Bug: convertCents truncates instead of rounding. Converting 1000 cents at a rate of 1.0875 (1087500 micros) gives 1087.5, which must round to 1088 cents, but convertCents returns 1087.
