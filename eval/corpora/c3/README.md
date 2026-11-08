# C3: real agent pull requests (not run yet)

PRD section 17.1 defines C3 as 50 merged pull requests from public repositories that were
written with coding agents. They are assumed mostly honest and are used to measure false
alarms on real code.

**Status: the harness supports C3, but the corpus has not been run.** Running it clones
unknown repositories, installs their dependencies and executes their test suites, so it must
not run on a developer machine without a sandbox. Run it on an isolated CI runner or a
throwaway VM.

`candidates.json` lists 44 merged Python pull requests whose description says "Generated
with Claude Code" (one per repository). It holds GitHub search metadata only. Nobody has yet
checked which of them have a runnable pytest suite.

To run C3:

1. Pick candidates with a pytest suite that installs cleanly. For each, record the merge
   base and the merged head commit.
2. Copy `corpus.example.toml` to `corpus.toml` and add one `[[cases]]` entry per pull request.
3. On an isolated runner, install each project's test dependencies, then run:

   ```sh
   counterproof eval eval/corpora/c3 --allow-remote --jobs 2
   ```

Disagreements (honest pull requests that Counterproof flags) should be reviewed by hand, as
the PRD asks.
