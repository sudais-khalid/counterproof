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

> **Status: pre-alpha (milestone M5).** Counterproof resolves the change, snapshots your
> working tree without touching it, builds isolated worktrees, selects impacted tests from
> coverage and runs the head baseline. All five probes run: **claim audit** (the agent's
> final message against the commands in its Claude Code transcript), **integrity**,
> **red-green**, **mutants** (rule-based plus, with an LLM provider, LLM-written realistic
> faults and an overfit mutant, filtered for equivalence by a static check, an LLM judge and
> Hypothesis fuzzing) and the **blind oracle** (with an LLM provider: tests written from the
> task and the public interface only, run on the change and triaged when they fail). The
> deterministic verdict engine implements all 14 rules of PRD section 8.
> See [docs/PRD.md](docs/PRD.md).

```sh
counterproof verify                      # certificate in the terminal, exit code = verdict
counterproof verify --format md          # Markdown certificate (PRD section 14)
counterproof verify --fail-on refuted    # CI gate: only REFUTED fails the job
counterproof report RUN_ID --format json # re-render a stored run
counterproof explain RUN_ID r002         # diff, tests run and hint for one mutant
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

## Development

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
