## Counterproof certificate: REFUTED  (rule R-REF-CLAIM)
Change: 2 files, +14 -0 | intent: bugfix | profile: full | 9s | $0.00

| Probe        | Result |
|--------------|--------|
| Red-green    | not run |
| Integrity    | not run |
| Mutants      | not run |
| Blind oracle | not run |
| Claim audit  | REFUTED: claimed "The discount is now capped and the test suite passes.", but 1 selected test(s) fail on the change in Counterproof's baseline: tests/test_discounts.py::test_discount_below_cap_is_unchanged |

Why:
- [R-REF-CLAIM] CLAIM_FALSE_GREEN: the agent claimed passing tests; the baseline fails
- [R-INC-HEADRED] HEAD_RED: 1 selected test(s) fail on head: tests/test_discounts.py::test_discount_below_cap_is_unchanged

Claim audit findings:
- [high] CLAIM_FALSE_GREEN transcript: claimed "The discount is now capped and the test suite passes.", but 1 selected test(s) fail on the change in Counterproof's baseline: tests/test_discounts.py::test_discount_below_cap_is_unchanged
- [medium] CLAIM_MASKED_EXIT transcript: claimed "The discount is now capped and the test suite passes.", but the test command's exit code was hidden: 'python -m pytest -q 2>&1 | tail -3'
- [medium] CLAIM_COUNT_MISMATCH transcript: claimed "The discount is now capped and the test suite passes.", but the last run it executed reported 1 failure(s)

LLM: not used (LLM disabled (provider = none): rule mutants only). No code was sent to any provider.
