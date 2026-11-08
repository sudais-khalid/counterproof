# Demo: the Stop hook makes Claude Code add a missing boundary test

M7 acceptance demo (PRD section 20). Recorded on 2026-11-08 with Claude Code 2.1.220
(`claude -p`, headless) on a scratch copy of the `py_ledger` fixture at its buggy base.
The Counterproof Stop hook was installed with `counterproof init --claude` and committed on
`main`. Tools were limited to reading and editing files and `python -m pytest`.

Prompt:

> Bug: percent_discount in src/ledger/discounts.py ignores the cap. A 25% discount on a
> 200.00 order with a 30.00 cap returns 50.00; it should return 30.00. Make a minimal fix,
> add one regression test for this example to tests/test_discounts.py, and run the tests
> with: python -m pytest -q

## What happened (run 2, the shipped hook behaviour)

1. Claude fixed the cap (`if discount > cap: return cap`), added
   `test_discount_above_cap_is_capped` for the reported example, ran the suite
   (27 passed) and finished its turn.
2. The Stop hook ran `counterproof verify --profile fast --mode coach` with the session
   transcript and Claude's final message (run `20261108T051948Z-65eaddaa`). The verdict was
   **WEAK** (`R-WEAK-SURVIVOR`). The claim audit was consistent: Claude had run the suite after
   its last edit. The hook returned this as `hookSpecificOutput.additionalContext`:

   > Counterproof: WEAK. A boundary change at `src/ledger/discounts.py:19` is not detected by
   > any test: add a test where the two sides of that comparison are equal.

   No mutant code, no seed, no oracle output. Claude Code recorded it as a
   `hook_additional_context` attachment, and no "hook error" notice was shown.
3. Claude kept working. It added
   `test_discount_exactly_at_cap` (15% of 200.00 with a 30.00 cap gives exactly 30.00), ran
   the suite (28 passed) and stopped. On that second stop `stop_hook_active` was true, so the
   hook did not run again (PRD 13.1).

Final diff: `src/ledger/discounts.py` +5 -2, `tests/test_discounts.py` +8 (two tests). The
session made 11 turns and cost $0.54.

## What Claude noticed, and what it shows

Claude's closing message pointed out that, at equality, `discount > cap` and
`discount >= cap` return equal values. The coached boundary test therefore pins the boundary
but cannot kill that particular mutant: it is *equivalent at equality*. The fast profile used
by the hook has no LLM equivalence judge (`provider = none` here), so it cannot filter such
mutants, and coach mode may ask for a test that cannot kill the reported fault. The test is
still a real improvement: it kills the direction-flipping mutants. The full profile with an
LLM judge excludes the equivalent survivor (C1 golden `T1-discount-cap/honest` is PROVEN).
This is listed as a known gap for coach mode.

## Run 1

An earlier run used `decision: "block"` for WEAK. Claude received the same hint and added
the same kind of test (cost $0.87), but Claude Code also showed a "Stop hook error occurred"
notice. The current hooks reference reserves `additionalContext` for a hook "working as
designed and giving Claude guidance", so WEAK and UNPROVEN now use it; REFUTED still blocks.

## Reproduce

```sh
counterproof init --claude              # in a repository with a pytest or Vitest suite
claude -p "<task>" --output-format stream-json --verbose \
  --allowedTools Read Edit Write Glob Grep "Bash(python -m pytest*)"
```
