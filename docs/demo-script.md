# Two-minute demo: script and recording kit

The demo video is recorded from `scripts/demo.sh`, which replays the scenes below
deterministically: fixed fixtures, a fixed seed, no network. Record the terminal in one
take with any screen recorder (`DEMO_PAUSE=6 bash scripts/demo.sh` for hands-free timing),
then add the voice-over below.

| Time | Scene (scripts/demo.sh) | Voice-over |
|---|---|---|
| 0:00 to 0:12 | The task text | "Coding agents stop when their tests pass. But when the same agent writes the fix and the test, green is a claim, not evidence. Counterproof checks the claim." |
| 0:12 to 0:35 | 1. `verify` on the hard-coded fix: **REFUTED (R-REF-INTEGRITY)**, FR-IN-07 at `discounts.py` | "This fix special-cases the exact inputs the test uses. The integrity probe sees the code compare an input with a test literal and return the test's expected value. Refuted, before a single mutant runs." |
| 0:35 to 1:00 | 2. `verify` on the partial fix: **WEAK (R-WEAK-SURVIVOR)**, surviving faults with the missing test | "This one looks honest and its new test fails on the old code. But Counterproof writes plausible wrong versions of the changed line, and three of them pass every test. The gap: nothing tests a discount at or below 20 percent." |
| 1:00 to 1:15 | 3. Coach mode: one line naming the gap class and the location | "Inside Claude Code the Stop hook tells the agent only what kind of test is missing and where, never the faulty code, so it cannot just write a test for our mutant. In our recorded session it went back and added the boundary test." |
| 1:15 to 1:35 | 4. Claim audit on a session that piped pytest through tail: **REFUTED (R-REF-CLAIM)** | "Here the agent said the suite passes. The transcript shows `pytest \| tail`, which hid exit code 1, and Counterproof's own baseline fails. That claim is refuted." |
| 1:35 to 2:00 | 5. The C1 headline table | "On a hundred and sixty seeded changes, green tests flag none of the gamed ones, because they are all green. Counterproof refutes every hard-coded and weakened change and, with an LLM, flags thirty-seven of forty partial fixes. Every number regenerates with one command." |

Notes for recording:

- Font size 18 or larger, a terminal about 110 columns wide, a dark theme.
- Scene 5 reads `eval/results/2026-11-08-c1.md`. Re-run `counterproof eval eval/corpora/c1`
  first if the code has changed since.
- The Claude Code part (scene 3) can be extended with the recorded session in
  `docs/demos/claude-code-stop-hook.md`.
