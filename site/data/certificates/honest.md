## Counterproof certificate: PROVEN  (rule R-PROVEN)
Change: 2 files, +14 -0 | intent: bugfix | profile: full | 15s | $0.00

| Probe        | Result |
|--------------|--------|
| Red-green    | PASS: 2 of 3 test(s) fail on the old code (test_discount_is_capped, test_large_discount_on_small_order_is_capped) |
| Integrity    | OK (0 findings) |
| Mutants      | 6/7 killed (0.86). 1 survivor(s) |
| Blind oracle | not run (LLM disabled) |
| Claim audit  | skipped (no transcript given) |

Why:
- [R-PROVEN] tests fail on the old code and pass on the new; 6/7 mutants killed (0.86)

Surviving faults (would ship undetected):
1. src/ledger/discounts.py:18  `if cap is not None and discount > cap:` -> `if cap is not None and discount >= cap:`   (boundary, mutant r001)
   Missing test: Add a test where the two sides of the comparison on line 18 are equal.

Scope: Tested against 7 plausible fault(s) on the changed lines; LLM disabled: rule mutants only, no blind oracle; agent claims not audited (no usable transcript); not a proof of full program correctness.

LLM: not used (LLM disabled (provider = none): rule mutants only). No code was sent to any provider.
