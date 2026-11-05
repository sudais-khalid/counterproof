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

> **Status: pre-alpha (milestone M2).** Counterproof resolves the change, snapshots your
> working tree without touching it, builds isolated base and head worktrees, selects impacted
> tests from coverage, and runs the head baseline with flaky detection. Two probes run:
> **integrity** (weakened tests, skipped or neutralised assertions, deleted tests, code that
> special-cases test inputs or detects the test runner) and **red-green** (the change's tests
> must fail on the old code for a behavioural reason). Gamed changes are already `REFUTED`;
> honest changes report `INCONCLUSIVE (NOT_IMPLEMENTED)` until the mutant probe and full
> verdict engine land in M3. See [docs/PRD.md](docs/PRD.md) for the full plan.

Waive an integrity finding with an inline comment on (or just above) the flagged line:
`# counterproof: allow FR-IN-07 <reason>`. A waiver only applies once it is committed: a waiver
added by the change under test is reported as a proposal, so an agent cannot excuse itself.

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
