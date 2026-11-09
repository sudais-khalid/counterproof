<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sudais-khalid/counterproof/main/docs/assets/banner-dark.png">
    <img alt="Counterproof: green tests are a claim. Counterproof checks it." src="https://raw.githubusercontent.com/sudais-khalid/counterproof/main/docs/assets/banner-light.png" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://pypi.org/project/counterproof/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/counterproof?label=pypi&color=2850d6&cacheSeconds=3600"></a>
  <a href="https://pypi.org/project/counterproof/"><img alt="Supported Python versions" src="https://img.shields.io/pypi/pyversions/counterproof?color=2850d6&cacheSeconds=3600"></a>
  <a href="https://github.com/sudais-khalid/counterproof/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sudais-khalid/counterproof/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/sudais-khalid/counterproof/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/sudais-khalid/counterproof?color=2850d6&cacheSeconds=3600"></a>
  <a href="https://github.com/sudais-khalid/counterproof/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/pypi/l/counterproof?color=2850d6&cacheSeconds=3600"></a>
  <a href="https://sudais-khalid.github.io/counterproof/"><img alt="Website" src="https://img.shields.io/badge/website-docs%20%26%20results-0e1b2c"></a>
</p>

<p align="center">
  <b>Verify that an AI-written code change is actually proven by its tests, not just green.</b><br>
  Local-first. Open source. For pytest, Vitest and Jest.
</p>

<p align="center">
  <a href="https://sudais-khalid.github.io/counterproof/docs/">Docs</a> ·
  <a href="https://sudais-khalid.github.io/counterproof/integrations/">Integrations</a> ·
  <a href="#results">Results</a> ·
  <a href="https://sudais-khalid.github.io/counterproof/#demo-h">Demo video</a> ·
  <a href="https://sudais-khalid.github.io/counterproof/faq/">FAQ</a>
</p>

---

When the same AI agent writes the fix **and** the test, a passing suite proves very little.
Agents optimise for the visible check: published benchmarks such as
[EvilGenie](https://arxiv.org/abs/2511.21654) caught coding agents hard-coding test cases,
editing tests and fixing only the reported example. In our own recorded Claude Code sessions,
an agent told not to touch a contradictory test bent the code to satisfy it, then reported
"All 68 tests pass".

**Counterproof** checks the claim. It runs five probes against the change in isolated git
worktrees and returns a verdict that no language model decides.

<table>
  <tr>
    <td align="center"><b>98%</b><br><sub>of 120 seeded gamed changes flagged WEAK or worse, with an LLM</sub></td>
    <td align="center"><b>80 / 80</b><br><sub>hard-coded and weakened changes refuted</sub></td>
    <td align="center"><b>0%</b><br><sub>caught by "the tests are green"</sub></td>
    <td align="center"><b>27 s</b><br><sub>median run, $0.009 of LLM spend</sub></td>
  </tr>
</table>

<sub>Corpus C1, reproducible with <code>counterproof eval</code>. Strict numbers, real Claude Code sessions, real pull requests and the limits are under <a href="#results">Results</a>.</sub>

## Who it is for

- **Developers using Claude Code, Cursor, Copilot or any coding agent** who want to know whether
  "done, all tests pass" is true before they merge.
- **Teams reviewing agent pull requests**, with one certificate per pull request and a CI gate on
  the verdict.
- **People building or evaluating agents**, who need a reproducible, rule-based check for reward
  hacking on real code.

<p align="center">
  <img alt="Counterproof refutes a hard-coded fix, calls a partial fix WEAK with the missing test, and refutes a false 'tests pass' claim" src="https://raw.githubusercontent.com/sudais-khalid/counterproof/main/docs/assets/demo.svg" width="100%">
</p>

## Quick start

```sh
pip install counterproof         # or: pipx install counterproof / uv tool install counterproof
cd your-repo
counterproof verify              # the working tree against HEAD
```

Upgrade with `pip install --upgrade counterproof` (or `pipx upgrade` / `uv tool upgrade`);
`counterproof --version` prints what you have. Python 3.11 to 3.13 on Linux, macOS and Windows.

```sh
counterproof verify --base main --task-file task.md      # say what the change was for
counterproof verify --transcript session.jsonl           # audit the agent's claims too
counterproof verify --fail-on refuted                    # CI gate on evidence of gaming
counterproof verify --sandbox docker                     # tests in a no-network container
```

Nothing leaves your machine unless you configure an LLM provider. Counterproof never writes
to your working tree.

## What a certificate looks like

```text
## Counterproof certificate: WEAK  (rule R-WEAK-SURVIVOR)
Change: 2 files, +6 -0 | intent: bugfix | profile: full | 17s | $0.00

| Probe        | Result |
|--------------|--------|
| Red-green    | PASS: 1 of 1 test(s) fail on the old code (test_discount_is_capped) |
| Integrity    | OK (0 findings) |
| Mutants      | 5/8 killed (0.62). 3 survivor(s) |

Surviving faults (would ship undetected):
1. src/ledger/discounts.py:18  `rate > 20` -> `rate >= 20`   (boundary)
   Missing test: Add a test where the two sides of the comparison on line 18 are equal.
```

A partial fix whose own test fails on the old code and passes on the new, so it looks proven.
But wrong versions of the changed line pass every test too, and the certificate names the
test that would catch them.

## How it works

```mermaid
flowchart LR
    A[Change + task + transcript] --> W[Isolated worktrees<br/>base and head]
    W --> P5[P5 Claim audit]
    P5 --> P2[P2 Integrity]
    P2 --> P1[P1 Red-green]
    P1 --> P3[P3 Change-scoped mutants]
    P3 --> P4[P4 Blind oracle]
    P4 --> E{{Verdict engine<br/>14 ordered rules}}
    E --> V[PROVEN / WEAK / UNPROVEN /<br/>REFUTED / INCONCLUSIVE]
```

| Probe | Question it answers |
|---|---|
| **P5 Claim audit** | Do the agent's final claims match the commands its session actually ran? Catches "tests pass" with no run after the last edit, a subset sold as the full suite, an exit code hidden behind `\| tail`. |
| **P2 Integrity** | Did the change weaken a test (removed assertion, new skip, deleted test) or special-case the test's own inputs (`if amount == 200: return 30`)? |
| **P1 Red-green** | Do the change's tests fail on the old code for a behavioural reason, and pass on the new? |
| **P3 Mutants** | Would the tests notice a realistic wrong version of the changed lines? Rule and LLM mutants, filtered for equivalence by a static check, an LLM judge and fuzzing. |
| **P4 Blind oracle** | Do tests written from the task and the public interface alone (never a function body) agree with the change? |

The probes run cheapest first, and a decision that is final early skips the expensive ones.
A pure function maps the evidence to a verdict, and the exit code follows:

| Verdict | Meaning | Exit |
|---|---|---|
| `PROVEN` | Red on the old code, green on the new, and the tests catch wrong versions of the change | 0 |
| `WEAK` | Plausible, with a named gap: a surviving mutant, a likely bug from the oracle | 10 |
| `UNPROVEN` | The tests do not pin the change: they pass on the old code, or most wrong versions survive | 20 |
| `REFUTED` | Evidence of gaming: hard-coded outputs, a weakened test, a false claim of passing tests | 30 |
| `INCONCLUSIVE` | The tests could not run reliably: build error, red head, too flaky | 40 |

## Results

Every number below comes from `counterproof eval` and regenerates exactly: fixed seeds,
recorded DeepSeek answers replayed offline, and every corpus run twice with identical verdicts
and evidence. *Strict* counts UNPROVEN or REFUTED as flagged (the product requirements'
definition); *lenient* also counts WEAK. Full tables: [C1](https://github.com/sudais-khalid/counterproof/blob/main/eval/results/2026-11-08-c1.md),
[C2](https://github.com/sudais-khalid/counterproof/blob/main/eval/results/2026-11-08-c2.md), [C3](https://github.com/sudais-khalid/counterproof/blob/main/eval/results/2026-11-09-c3.md).

**C1: 160 seeded changes** in a Python and a TypeScript library: 40 tasks, each with an honest
fix and three gamed variants (hard-coded outputs, a weakened test, a partial fix), ground truth
checked with the real test runners.

| | Gamed flagged, strict | Gamed flagged, lenient | Honest flagged, strict | Honest flagged, lenient |
|---|---|---|---|---|
| Counterproof, rules only | 67% (80/120) | 78% (94/120) | 0% (0/40) | 25% (10/40) |
| **Counterproof, with an LLM** | **75% (90/120)** | **98% (117/120)** | **8% (3/40)** | 28% (11/40) |
| B0: the tests are green | 0% | 0% | 0% | 0% |
| B1: claim audit only (no transcripts in C1) | 0% | 0% | 0% | 0% |
| B2: diff-scoped classic mutation | 39% | 39% | 22% | 22% |

Every hard-coded and every weakened change is REFUTED (80/80). With an LLM, 37 of 40 partial
fixes are flagged, 10 of them UNPROVEN. Without the integrity probe, strict recall falls from 75%
to 23%; LLM mutants lift strict recall from 67% to 75% and lenient recall from 92% to 98%, and
the blind oracle lifts lenient recall from 96% to 98%.
Median run: 15 s rule-only, 27 s with an LLM, $0.009 of recorded LLM spend.

**C2: 23 agent sessions with their claims.** 15 are real Claude Code sessions (claude-sonnet-5,
headless) on eight bug reports, run twice: once plainly, once EvilGenie-style, where a given
test contradicts the task and the agent may not edit tests. Labels come from held-out tests the
agent never saw plus a hand review. Under pressure, 2 of 8 sessions bent the code to satisfy the
contradiction and reported "All 68 tests pass"; 5 reported the contradiction honestly; 1 made no
change. The other 8 sessions reproduce known false-claim patterns.

| | Gamed flagged, strict | Gamed flagged, lenient | Honest flagged, strict | Honest flagged, lenient |
|---|---|---|---|---|
| Counterproof, rules only | 25% (2/8) | 88% (7/8) | 0% (0/15) | 27% (4/15) |
| **Counterproof, with an LLM** | 25% (2/8) | **100% (8/8)** | 33% (5/15) | 33% (5/15) |
| B1: claim audit only | 88% | 88% | 0% | 0% |

The two real gamed sessions passed every visible test with truthful claims: only the blind
oracle (one) and the claim audit's hidden-exit-code check (the other) flagged them. The five
honest sessions flagged with an LLM are correct fixes whose single regression test pins only the
reported example, so a version special-cased to that example passes too: UNPROVEN means "not
proven by these tests", not "wrong".

**C3: 41 real merged pull requests** from public Python repositories whose description says
Claude Code wrote them, each run rule-only on its own fresh GitHub runner with its dependencies
installed best effort. They are assumed honest, so every flag counts as a false alarm until a
hand review says otherwise.

| Verdict | PROVEN | WEAK | UNPROVEN | REFUTED | INCONCLUSIVE |
|---|---|---|---|---|---|
| Pull requests | 0 | 3 | 5 | 1 | 32 |

Counterproof could run the tests of 9 of the 41; the other 32 are INCONCLUSIVE, mostly because
the project's tests do not collect on a fresh runner (22). Of the 9, a hand review found no
gaming. The five UNPROVEN are honest changes whose tests do not pin them: they catch 0 to 14% of
realistic wrong versions, or pass on the old code too. The one REFUTED adds a test that skips
itself when model weights are not downloaded (FR-IN-03), which looks the same as an agent
skipping a test it cannot pass. The C3 review also changed two things before release: comparing
an input with a one-word string (`== "rejected"`) is now medium FR-IN-07 for review, not a
refutation, after it caused every refutation in an earlier run; and tree-sitter is pinned below
0.26, whose 0.26.0 release segfaulted on three of these pull requests' TypeScript files.

**Against the v1 targets:**

| Metric | Target | Measured | |
|---|---|---|---|
| Gamed-change recall, strict | >= 85% | 75% (C1, LLM) | missed |
| Honest false alarms, strict | <= 15% | 8% (C1); 33% (C2 real sessions); 6 of 9 runnable (C3) | met on C1 |
| Lift over transcript checking | >= +30 points | +75 (C1); +12 lenient (C2) | met on C1 |
| Median runtime, full profile | <= 5 min | 27 s | met |
| Median LLM cost per run | <= $0.30 | $0.009 | met |
| Equivalent-mutant leakage | <= 10% | 10% (5 of 50 survivors, human-labelled) | met |
| Determinism, same seed | 100% | 100% (C1, C2) | met |

**Limits, stated plainly.**

- **Strict recall misses its target, by design of the verdict rules.** A partial fix whose tests
  catch most wrong versions is WEAK (rule 9), and partial fixes are a third of the gamed cases, so
  the rules as specified cap strict recall below 85%. Counterproof reports these partial fixes as
  WEAK with the exact missing test; it does not call them UNPROVEN.
- **Real pull requests are mostly INCONCLUSIVE on a fresh machine.** In C3, 32 of 41 were
  INCONCLUSIVE: 22 projects' tests did not collect after a best-effort install, 4 had no tests
  for the changed lines and 6 had other setup gaps; 3 more pull requests timed out. Counterproof
  runs the project's own environment; it does not install one. On the 9 it could run, 6 honest
  changes were flagged strict, 5 of them because their tests do not pin the change.
- **Corpora written by the tool's author.** C1 is seeded by the same author as the tool; C2's
  real sessions are 15, on the same fixture library. Treat them as evidence and a regression
  suite, not proof of accuracy on every code base.
- **UNPROVEN on honest changes.** Agents often add one test for the reported example. Counterproof
  then says the change is not proven, which is true, but it counts as a false alarm on an honest
  fix (5 of 15 real sessions, 3 of 40 seeded honest fixes).
- **One sample of LLM answers.** The LLM results replay one recorded sample of DeepSeek answers;
  another sample or model can move a few verdicts. One of the 811 recorded replies (an equivalence
  judgement) was invalid JSON and was rejected by the strict contract; that mutant stayed unjudged.
- **Missed partial fixes.** 3 of 40 partial fixes pass as PROVEN even with an LLM, for example
  giving every leftover cent to the first share, which no generated test or mutant probed with
  two or more leftover cents.
- **B2 is an approximation** of diff-scoped classic mutation (Counterproof's rule mutants with no
  equivalence filter), not mutmut or Stryker.
- **Docker sandbox:** Linux and macOS hosts; Node projects need a Linux host.

## Integrations

**Claude Code (coach mode).** `counterproof init --claude` adds a Stop hook. When Claude
finishes, the hook verifies the change with the session transcript: REFUTED blocks, and WEAK
or UNPROVEN returns one line naming the gap and its location, so Claude keeps working. Never
mutant code, oracle tests or the seed: the agent cannot write a test for our mutant.

> Counterproof: WEAK. A boundary change at `src/ledger/discounts.py:19` is not detected by
> any test: add a test where the two sides of that comparison are equal.

**GitHub Actions.** One certificate comment per pull request, SARIF for code scanning, a gate
on the verdict you choose. Pull requests from forks always run without LLM keys.

```yaml
- uses: actions/checkout@v7
  with: {fetch-depth: 0}
- run: pip install -e . pytest pytest-cov          # your project and its tests
- uses: sudais-khalid/counterproof/action@v1
  with:
    fail-on: refuted                               # or unproven / weak
```

**MCP.** `claude mcp add counterproof -- counterproof mcp` exposes `verify_change`,
`get_report` and `explain_mutant` to any MCP client, redacted for agents.

**git.** `counterproof init --git-hook --fail-on refuted` verifies every pushed range.

[Set up an integration](https://sudais-khalid.github.io/counterproof/integrations/)

## LLM providers and privacy

LLMs are optional and only propose mutants, judge equivalence and write oracle tests.

| Provider | Key | Default models |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5`, `claude-haiku-5-5` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-pro`, `deepseek-flash` |
| `openai` | `OPENAI_API_KEY` | set the models explicitly |
| `ollama` | none, local | `qwen2.5-coder:7b` |

Only the changed lines, their context, the task text and test snippets are sent; secrets are
redacted and `.env` files, lockfiles and excluded paths never are. Every run has hard token,
dollar (0.30 USD by default) and time budgets, answers are cached, and the certificate names
the provider that saw code. The blind oracle prefers a model family other than the one that
wrote the change. Configuration is read from the **base** commit, so a change cannot loosen its
own thresholds. See [the docs](https://sudais-khalid.github.io/counterproof/docs/#configuration).

## How it compares

| | Green CI | Coverage | Classic mutation testing | Transcript review | **Counterproof** |
|---|:-:|:-:|:-:|:-:|:-:|
| Tests fail on the old code | | | | | ✓ |
| Weakened or special-cased tests | | | | partly | ✓ |
| Wrong versions of the change go unnoticed | | | ✓ (whole code base, noisy) | | ✓ (changed lines, filtered) |
| Tests written without seeing the code | | | | | ✓ |
| The agent's claims match what it ran | | | | ✓ | ✓ |
| One verdict a CI job can gate on | ✓ | threshold | score | | ✓ |

## Development

```sh
uv sync && npm ci --prefix tests/fixtures/node
uv run ruff check && uv run ruff format --check && uv run mypy
uv run pytest -q -n auto -m "not corpus"     # every CI job
uv run pytest -q -n auto -m corpus           # the extended C1 corpus
```

CI runs on Linux, macOS and Windows with Python 3.11 and 3.13, offline: real LLM answers are
replayed from recorded cassettes. See [CONTRIBUTING.md](https://github.com/sudais-khalid/counterproof/blob/main/CONTRIBUTING.md),
[SECURITY.md](https://github.com/sudais-khalid/counterproof/blob/main/SECURITY.md) and the [changelog](https://github.com/sudais-khalid/counterproof/blob/main/CHANGELOG.md). The product requirements are in
[docs/PRD.md](https://github.com/sudais-khalid/counterproof/blob/main/docs/PRD.md).

## Licence

Apache-2.0. See [LICENSE](https://github.com/sudais-khalid/counterproof/blob/main/LICENSE). Built by [Sudais Khalid](https://sudaiskhalid.com).
