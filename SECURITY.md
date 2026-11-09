# Security policy

## Supported versions

The latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/sudais-khalid/counterproof/security/advisories/new),
not in a public issue. Expect an acknowledgement within a few days. Once a fix is released,
the advisory is published with credit, unless you prefer otherwise.

## What Counterproof does with your code

- **Local by default.** With `llm.provider = "none"` (the default) no code leaves your machine.
- **When an LLM is configured**, only changed lines, their context, the task text and test
  snippets are sent. Secret-shaped strings are redacted, and `.env` files, lockfiles and
  `paths.exclude` are never sent. Every certificate names the provider that saw code.
- **Keys** come from the environment or a `credentials.toml` in your user cache directory,
  never from the repository.
- **Your tests run on your machine.** Counterproof executes the project's own test suite and
  mutated copies of the changed lines, in worktrees under the cache directory, with a scrubbed
  environment. Verify only code you would run yourself, or use `--sandbox docker`.
- **LLM-written tests are checked before they run.** Blind-oracle tests are rejected if they
  import outside the interface, touch files, processes, the network or timers, or reach
  private names.
- **The GitHub Action** runs pull requests from forks with `provider: none`, so keys never
  reach untrusted code.
