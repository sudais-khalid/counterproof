# Counterproof

**Does the test actually prove the change?** Coding agents stop when their checks pass. When
the same agent writes both the fix and the test, a green suite is a claim, not evidence.
Counterproof runs after the agent finishes and decides, with a deterministic rule table,
whether the tests demonstrably distinguish the change from plausible wrong versions of it.

Verdicts: `PROVEN`, `WEAK`, `UNPROVEN`, `REFUTED`, `INCONCLUSIVE`. An LLM never decides the
verdict.

## How it works

Everything runs in isolated git worktrees outside your repository. Your working tree is never
touched.

| Probe | Question it answers |
|---|---|
| P5 Claim audit | Does the agent's final message ("all 42 tests pass") match what its session shows it ran? |
| P2 Integrity | Were tests weakened, skipped or deleted? Does the code special-case test inputs or detect the test runner? |
| P1 Red-green | Do the change's tests fail on the old code for a behavioural reason and pass on the new code? |
| P3 Mutants | Do the tests kill plausible wrong versions of exactly the changed lines (rule-based and LLM-written, with an overfit mutant and a 3-stage equivalence filter)? |
| P4 Blind oracle | Do tests written from the task and public interface alone, without seeing the code, agree with it? |

The verdict engine is a pure function of these results (PRD section 8). Each run is sealed as
an evidence bundle with a sha256 manifest, and the run seed is kept secret behind a commitment.

## Quick start

```sh
uv sync && uv run counterproof doctor
uv run counterproof verify                  # in your repository, after the agent's change
uv run counterproof verify --format md      # the proof certificate
uv run counterproof init --claude           # coach mode inside Claude Code
```

Languages: Python (pytest) and TypeScript/JavaScript (Vitest, Jest). LLM providers are
optional (`deepseek`, `anthropic`, `openai`, `ollama`). With `provider = none` nothing leaves
your machine.

## Results

On corpus C1 (160 seeded changes in a Python and a TypeScript library), green tests flag none
of the 120 gamed changes. Counterproof refutes all 80 hard-coded and weakened-test variants and,
with recorded LLM output, flags 37 of 40 partial fixes as WEAK or worse, while refuting or
calling unproven 3 of 40 honest fixes. Real Claude Code sessions (corpus C2) and real agent pull
requests (corpus C3) are measured too. The website has the full results and the honest limits:
<https://sudais-khalid.github.io/counterproof/results/>, with the tables in
[eval/results](../eval/results/). Everything regenerates with:

```sh
counterproof eval eval/corpora/c1
```

## Integrations

- **Claude Code** Stop hook (coach mode): names the missing test's class and location, never
  the answer. See the [recorded session](demos/claude-code-stop-hook.md).
- **GitHub Action**: comments one certificate per pull request and uploads SARIF. See the
  [demo](demos/github-action.md).
- **MCP server**, **git pre-push hook**, **HTML and SARIF reports**.

## Design document

The full product requirements, including the anti-gaming design: [PRD](PRD.md).
