## Counterproof certificate: WEAK  (rule R-WEAK-SURVIVOR)
Change: 2 files, +6 -0 | intent: bugfix | profile: full | 21s | $0.00

| Probe        | Result |
|--------------|--------|
| Red-green    | PASS: 1 of 1 test(s) fail on the old code (test_discount_is_capped) |
| Integrity    | OK (0 findings) |
| Mutants      | 5/8 killed (0.62). 3 survivor(s) |
| Blind oracle | not run (LLM disabled) |
| Claim audit  | skipped (no transcript given) |

Why:
- [R-WEAK-SURVIVOR] some realistic wrong versions survive: 5/8 mutants killed (0.62)

Surviving faults (would ship undetected):
1. src/ledger/discounts.py:18  `if cap is not None and rate > 20 and discount > cap:` -> `if cap is not None and rate > 20 and discount >= cap:`   (boundary, mutant r001)
   Missing test: Add a test where the two sides of the comparison on line 18 are equal.
2. src/ledger/discounts.py:18  `if cap is not None and rate > 20 and discount > cap:` -> `if cap is not None and rate >= 20 and discount > cap:`   (boundary, mutant r002)
   Missing test: Add a test where the two sides of the comparison on line 18 are equal.
3. src/ledger/discounts.py:18  `if cap is not None and rate > 20 and discount > cap:` -> `if cap is not None and rate > 21 and discount > cap:`   (constant, mutant r003)
   Missing test: Add a test that pins the constant on line 18; a nearby value should fail.

Scope: Tested against 8 plausible fault(s) on the changed lines; LLM disabled: rule mutants only, no blind oracle; agent claims not audited (no usable transcript); not a proof of full program correctness.

LLM: not used (LLM disabled (provider = none): rule mutants only). No code was sent to any provider.
