# Demo: the GitHub Action comments a certificate on a pull request

M7 acceptance demo (PRD section 20), recorded on 2026-11-08 in this repository.

A throwaway pull request ([#1](https://github.com/sudais-khalid/counterproof/pull/1), closed
without merging, branch deleted) added one workflow. On a GitHub-hosted runner it built the
`py_ledger` fixture with the T1 *partial* fix (caps the discount only above 20 %), then ran
`./action` on it:

```yaml
- uses: ./action
  with:
    working-directory: ${{ steps.fixture.outputs.path }}
    base: ${{ steps.fixture.outputs.base }}
    head: ${{ steps.fixture.outputs.head }}
    upload-sarif: "false"   # the fixture's paths do not exist in this repository
    fail-on: refuted
```

The pull request body held the task text, so the Action passed it to `verify --task-file`.
Results:

- Run 42453388217 succeeded. Every step of the composite action ran: install into its own
  venv, verify, comment, gate.
- The Action posted one comment, marked `<!-- counterproof-certificate -->`:
  **WEAK (R-WEAK-SURVIVOR)**, red-green PASS, integrity OK, 5/8 mutants killed. The three
  survivors are the `rate > 20` boundary and constant gaps, each with its missing test.
- An empty commit pushed to the branch re-ran the workflow. The PR still had **one** comment,
  updated with the new run id (`20261108T060838Z-9bcf18c9`).
- With `fail-on: refuted` a WEAK verdict passes the job. `fail-on: weak` would fail it after
  the comment is posted.
