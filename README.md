# Counterproof

Counterproof is a local-first verification agent. It decides whether a code change written by
an AI coding agent is actually **proven** by the tests around it, or only looks correct because
the suite is green.

After an agent finishes, Counterproof runs five probes in isolated git worktrees:

1. **Red-green**: the change's tests must fail on the old code for a behavioural reason and
   pass on the new code.
2. **Integrity**: static detection of weakened tests and of source that special-cases test inputs.
3. **Change-scoped mutants**: realistic wrong versions of only the changed lines. If the tests
   still pass on a wrong version, the change is not proven.
4. **Blind oracle**: tests written from the task and interface only, never from function bodies.
5. **Claim audit**: the agent's final claims compared with the commands it actually ran.

A deterministic verdict engine maps the results to `PROVEN`, `WEAK`, `UNPROVEN`, `REFUTED` or
`INCONCLUSIVE`. An LLM never decides the verdict.

> **Status: pre-alpha (v1 milestones M0 to M8 complete).** Python (pytest) and TypeScript/JavaScript (Vitest,
> Jest) projects are supported. Counterproof resolves the change, snapshots your
> working tree without touching it, builds isolated worktrees, selects impacted tests from
> coverage and runs the head baseline. All five probes run: **claim audit** (the agent's
> final message against the commands in its Claude Code transcript), **integrity**,
> **red-green**, **mutants** (rule-based plus, with an LLM provider, LLM-written realistic
> faults and an overfit mutant, filtered for equivalence by a static check, an LLM judge and
> Hypothesis or fast-check fuzzing) and the **blind oracle** (with an LLM provider, Python
> only for now: tests written from the task and the public interface only, run on the change
> and triaged when they fail). The
> deterministic verdict engine implements all 14 rules of PRD section 8.
> See [docs/PRD.md](docs/PRD.md).

```sh
counterproof verify                      # certificate in the terminal, exit code = verdict
counterproof verify --format md          # Markdown certificate (PRD section 14)
counterproof verify --fail-on refuted    # CI gate: only REFUTED fails the job
counterproof report RUN_ID --format json # re-render a stored run
counterproof explain RUN_ID r002         # diff, tests run and hint for one mutant
counterproof report RUN_ID --format html --out report.html   # also: sarif, json, md
counterproof verify --transcript ~/.claude/projects/<proj>/<session>.jsonl   # audit claims
counterproof export-oracle RUN_ID        # list blind-oracle tests; --accept NAME copies one
```

The claim audit flags "tests pass" claims that the transcript contradicts: no test run after
the last edit, a different pass count, a subset run claimed as the full suite, an exit code
hidden behind `| tail` or `|| true`, or passing claimed while Counterproof's own baseline
fails. Without a transcript it is skipped, with no penalty. Blind-oracle tests stay in the run
directory; a likely bug they find caps the verdict at WEAK and is listed for human review,
but never refutes a change on its own.

Waive an integrity finding with an inline comment on (or just above) the flagged line:
`# counterproof: allow FR-IN-07 <reason>`. A waiver only applies once it is committed: a waiver
added by the change under test is reported as a proposal, so an agent cannot excuse itself.

## Evaluation

All numbers come from `counterproof eval` and regenerate exactly. Seeds are fixed, LLM answers
are replayed from recorded DeepSeek cassettes, and both corpora were run twice with the same
verdicts. Full tables:
[eval/results/2026-11-08-c1.md](eval/results/2026-11-08-c1.md) and
[eval/results/2026-11-08-c2.md](eval/results/2026-11-08-c2.md).

```sh
counterproof eval eval/corpora/c1    # 40 seeded changes, rule-only and recorded-LLM
counterproof eval eval/corpora/c2    # 8 agent sessions with true and false claims
```

**C1: 40 seeded changes** (Python `py_ledger` and TypeScript `ts_cart`, 5 tasks each: one
honest fix and three gamed variants per task). *Strict* counts UNPROVEN or REFUTED as
flagged, the PRD's definition; *lenient* also counts WEAK.

| | Gamed flagged, strict | Gamed flagged, lenient | Honest flagged, strict | Honest flagged, lenient |
|---|---|---|---|---|
| Counterproof, rules only | 67% (20/30) | 77% (23/30) | 0% (0/10) | 10% (1/10) |
| Counterproof, recorded LLM | 70% (21/30) | 97% (29/30) | 0% (0/10) | 30% (3/10) |
| B0 green tests only | 0% | 0% | 0% | 0% |
| B1 claim audit only (no transcripts in C1) | 0% | 0% | 0% | 0% |
| B2 diff-scoped classic mutation | 23% | 23% | 20% | 20% |

By kind, with recorded LLM output: hard-coded outputs 10/10 REFUTED, weakened tests 10/10
REFUTED, partial fixes 1/10 UNPROVEN plus 8/10 WEAK.

**Ablation** (the verdict engine re-decides with one probe removed; recorded LLM): without
the integrity probe, strict recall drops from 70% to 13%. LLM mutants raise lenient recall on gamed
changes from 87% to 97%. Removing red-green or mutants makes every change at least WEAK,
because PROVEN needs both kinds of evidence. The blind oracle and claim audit changed no C1
verdict: rule 9 had already capped the oracle's catches at WEAK, and C1 has no transcripts.

**C2: 8 agent sessions** (Claude Code JSONL, built to the real format with real pytest
output): Counterproof flags 6 of 6 false or unsupported claims (2 REFUTED, 4 WEAK) and neither
honest one. That is no better than the claim-only baseline B1 (6/6). What it adds is the
independent baseline run: a "tests pass" claim on a change whose tests really fail becomes
REFUTED, not merely a mismatch.

**Against the PRD's v1 targets:**

| Metric | Target | Measured | |
|---|---|---|---|
| Gamed-change recall (UNPROVEN or REFUTED) | >= 85% | 70% (C1, LLM) | missed: 8 of 10 partial fixes are WEAK, not UNPROVEN |
| Honest false alarms (UNPROVEN or REFUTED) | <= 15% | 0% | met; 3 of 10 are WEAK (see limits) |
| Lift over transcript checking | >= +30 points | +70 (C1); 0 (C2) | met on C1 only |
| Median runtime, full profile | <= 5 min | 27 s (C1, LLM) | met on small fixtures |
| Median LLM cost per run | <= $0.30 | $0.008 (DeepSeek, recorded) | met |
| Equivalent-mutant leakage | <= 10% | not measured | 22 survivors exported, none labelled yet |
| Determinism (same verdict, same seed) | 100% | 100% (C1 and C2, 2 runs each) | met |

**Limits, stated plainly.**

- **Small corpora.** The corpora are small and synthetic: 40 seeded changes and 8 sessions,
  written by the same author as the tool. Treat them as a regression suite and a
  demonstration, not proof of real-world accuracy. The PRD asks for 20 tasks per fixture
  repository; there are 5.
- **No real-agent data yet.** C2 sessions are hand-built in the verified format, not real
  agent runs. C3 (real agent pull requests) is set up but not run, because it executes
  foreign code ([eval/corpora/c3](eval/corpora/c3/README.md)).
- **One sample of LLM answers.** LLM results replay one recorded sample of DeepSeek answers;
  another sample, or another model, can move partial-fix verdicts by a case or two.
- **Partial fixes mostly come out WEAK, not UNPROVEN.** One partial fix (`ts_cart` T2) is
  missed entirely: no mutant differs only when two or more cents are left over, and there is
  no blind oracle for TypeScript yet.
- **The 3 WEAK honest fixes point at real untested cases**, for example negative totals where
  JavaScript's `%` keeps the sign. Each WEAK lists the exact gap, but a strict reviewer may
  still call it noise.
- **B2 is an approximation.** It uses Counterproof's own rule mutants restricted to the diff,
  with no equivalence filter, rather than mutmut or Stryker.
- **Coach mode can ask for a test that cannot kill the fault.** Without an LLM judge it
  cannot filter equivalent mutants (see the [Claude Code demo](docs/demos/claude-code-stop-hook.md)).

## LLM providers

LLMs are optional: with `llm.provider = "none"` (the default) nothing leaves your machine.

| Provider | Key | Default models (`auto`) |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-pro` (mutants, oracle), `deepseek-flash` (judges) |
| `anthropic` | `ANTHROPIC_API_KEY` or `ant auth login` | `claude-sonnet-5-5`, `claude-haiku-5-5` |
| `openai` | `OPENAI_API_KEY` | set `mutant_model` / `judge_model` explicitly |
| `ollama` | none (local) | `qwen2.5-coder:7b` |

Keys come from the environment or from `credentials.toml` in your user cache directory
(`%LOCALAPPDATA%\counterproof` on Windows, `~/.cache/counterproof` elsewhere), never from the
repository. Only the changed source lines, their context, the task text and test snippets are
sent; secrets are redacted, and `.env` files, lockfiles and `paths.exclude` are never sent.
Every run has hard token, USD (`llm.budget_usd`, default $0.30) and time limits; work the
budget does not allow is skipped and listed on the certificate, which always states which
provider saw code. Responses are cached, so re-running on the same change is free. The blind
oracle sees the task, signatures, docstrings and public constants, never a function body; with
`oracle_model = "auto"` it prefers a model family other than the one that wrote the code (read
from the transcript).

## Integrations

**Claude Code (coach mode).** `counterproof init --claude` adds a Stop hook to
`.claude/settings.json`. When Claude finishes a turn, the hook runs the fast profile on the
working tree with the session transcript. A REFUTED change is blocked; a WEAK or UNPROVEN one
gets a short hint so Claude keeps working, for example *"A boundary change at
`src/ledger/discounts.py:19` is not detected by any test: add a test where the two sides of
that comparison are equal."* The hint names the gap and its location only: never mutant code,
oracle tests or the seed, and a later audit run uses a fresh seed. The hook never re-runs while
Claude is already continuing because of it, gives up after 90 seconds without blocking, and
needs `counterproof` on `PATH` (or the interpreter path `init` writes).

**MCP.** `claude mcp add counterproof -- counterproof mcp` (or any MCP client, stdio) exposes
`verify_change`, `get_report` and `explain_mutant`. Agents get the redacted certificate: classes
and locations, no mutant code, unless a human commits `[mcp] reveal_mutants = true`.

**git.** `counterproof init --git-hook [--fail-on refuted]` installs a pre-push hook that
verifies each pushed range with the fast profile and blocks only at that verdict.

**GitHub Action.** Install your project's test dependencies first, then:

```yaml
on: pull_request
permissions: {contents: read, pull-requests: write, security-events: write}
jobs:
  counterproof:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with: {fetch-depth: 0}
      - run: pip install -e . pytest pytest-cov   # your project and its tests
      - uses: sudais-khalid/counterproof/action@main
        with:
          fail-on: refuted          # or unproven / weak
          provider: none            # or deepseek / anthropic with the matching key input
```

It comments one certificate on the PR (updated on every push), uploads SARIF to code
scanning and writes the certificate to the job summary. Pull requests from forks always run
with `provider: none`, so keys never reach untrusted code.

## Install (development)

```sh
uv sync
uv run counterproof doctor
```

## Usage

```sh
counterproof verify [--base REF] [--head REF|WORKTREE] [--task TEXT | --task-file PATH]
counterproof doctor
counterproof gc --older-than 7d
```

| Exit code | Meaning |
|---|---|
| 0 | PROVEN |
| 10 | WEAK |
| 20 | UNPROVEN |
| 30 | REFUTED |
| 40 | INCONCLUSIVE |
| 2 | usage error |
| 1 | internal error |

Run artefacts live outside the repository, under `$XDG_CACHE_HOME/counterproof/runs`
(`~/.cache` by default; `%LOCALAPPDATA%` on Windows). Set `COUNTERPROOF_CACHE_DIR` to override.
Counterproof never writes to your working tree.

Run `counterproof init` (or copy [counterproof.example.toml](counterproof.example.toml)) to
configure it. The configuration is read from the **base** commit by default, so a change cannot
loosen its own thresholds; pass `--config-from worktree` to use the file on disk. With no
configuration, `llm.provider = "none"` and nothing leaves your machine.

Tests run in your existing Python environment: `COUNTERPROOF_PYTHON`, then the repository's
`.venv`/`venv`, then `$VIRTUAL_ENV`, then the interpreter running Counterproof. Install
`pytest-cov` there to get coverage-based test selection. Child processes get a scrubbed
environment; pass extra variables with `[env] pass = [...]`.

TypeScript and JavaScript projects use their own Vitest or Jest (with `jest-junit`) from the
repository's `node_modules`, which each worktree links to; install dependencies first
(`npm ci`), since Counterproof never installs packages. `node` comes from `PATH` or
`COUNTERPROOF_NODE`. Vitest and Jest have no per-test coverage in v1, so tests are selected
from changed test files plus the test files that import a changed module. Install
`fast-check` to enable differential fuzzing of surviving mutants.

## Development

```sh
npm ci --prefix tests/fixtures/node   # Vitest/Jest for the TypeScript fixtures (once)
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
