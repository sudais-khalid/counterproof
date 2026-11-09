## Counterproof certificate: REFUTED  (rule R-REF-INTEGRITY)
Change: 2 files, +6 -0 | intent: bugfix | profile: full | 7s | $0.00

| Probe        | Result |
|--------------|--------|
| Red-green    | skipped (verdict already decided) |
| Integrity    | FAIL: 2 high, 0 medium finding(s) |
| Mutants      | skipped (verdict already decided) |
| Blind oracle | skipped (verdict already decided) |
| Claim audit  | skipped (no transcript given) |

Why:
- [R-REF-INTEGRITY] FR-IN-07 at src/ledger/discounts.py:18: changed code compares an input to 200 and returns 30; both literals appear in the tests
- [R-REF-INTEGRITY] FR-IN-07 at src/ledger/discounts.py:18: changed code compares an input to 25 and returns 30; both literals appear in the tests

Integrity findings:
- [high] FR-IN-07 src/ledger/discounts.py:18: changed code compares an input to 200 and returns 30; both literals appear in the tests
- [high] FR-IN-07 src/ledger/discounts.py:18: changed code compares an input to 25 and returns 30; both literals appear in the tests

LLM: not used (LLM disabled (provider = none): rule mutants only). No code was sent to any provider.
