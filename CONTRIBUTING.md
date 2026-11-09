# Contributing to Counterproof

Thanks for helping. Counterproof makes claims about other people's code, so the bar for its
own evidence is high: every change comes with tests, and no test is loosened to make a change
pass.

## Set up

```sh
git clone https://github.com/sudais-khalid/counterproof
cd counterproof
uv sync                                  # Python 3.11+, the dev tools included
npm ci --prefix tests/fixtures/node      # Vitest and Jest for the TypeScript fixtures
uv run counterproof doctor
```

## Before you open a pull request

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q -n auto -m "not corpus"     # what every CI job runs
uv run pytest -q -n auto -m corpus           # the extended C1 corpus (one CI job)
```

CI runs on Linux, macOS and Windows with Python 3.11 and 3.13, and needs no API key: real
LLM answers are replayed from the recorded cassettes in `tests/cassettes/`.

## Ground rules

- **The verdict engine stays pure.** `verdict/rules.py` decides from evidence only; an LLM
  can propose mutants or tests, never a verdict.
- **Never touch the user's working tree.** Everything runs in worktrees under the cache
  directory.
- **Anti-gaming holds everywhere an agent can read**: coach hints, hook output and MCP
  responses never contain mutant code, oracle tests or the seed.
- **Tests are evidence.** Do not delete or weaken a test, or change a golden expectation,
  without saying why in the pull request. Fixture behaviour is never hard-coded.
- **Requirement ids are traceable.** Code and test names cite the PRD ids they implement
  (for example `FR-IN-07`); see [docs/PRD.md](docs/PRD.md).
- **Prompts are versioned.** Changing a prompt changes the cache keys; re-record the
  cassettes (`tests/golden/record_cassettes.py`) in the same pull request.

## Fixtures and corpora

C1 tasks live in `tests/fixtures/repos/<repo>/tasks/` with four variants each, and their
ground truth in `tests/golden/expectations.yaml`. Tasks T6 to T20 are generated from
`tests/golden/c1_tasks_py.py` and `c1_tasks_ts.py` by `tests/golden/make_c1_tasks.py`; edit
the data and re-run the script rather than the generated files.

## Reporting bugs

Open an issue with the command you ran, the certificate (`--format md`) and, if you can, the
run directory from `counterproof report RUN_ID --format json`. For security issues, see
[SECURITY.md](SECURITY.md).
