## What and why

## Evidence

- [ ] `ruff check`, `ruff format --check`, `mypy` and `pytest -m "not corpus"` pass locally
- [ ] New behaviour has tests that fail without the change
- [ ] No test was deleted or weakened, and no golden expectation changed (or the reason is above)
- [ ] Prompt changes come with re-recorded cassettes
