# Changelog

All notable changes to Counterproof. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

- **Citable**: Counterproof is archived on Zenodo with the DOI
  [10.5281/zenodo.24102199](https://doi.org/10.5281/zenodo.24102199) (all versions; 1.0.1 is
  10.5281/zenodo.24102200). `CITATION.cff` drives GitHub's "Cite this repository" button, and
  `.zenodo.json` sets the archive's title, author, keywords and links for future releases. The
  README, the website and the package metadata link the DOI.

## [1.0.1] - 2026-11-09

Documentation, website and release plumbing; the verification engine is unchanged.

- **GitHub Action**: `uses: sudais-khalid/counterproof/action@v1` now resolves. Every release
  moves the `v1` tag to itself, and CI runs the action end to end on a seeded hard-coded fix,
  which it must refute.
- **Fixed: the action stopped at its own verdict.** GitHub runs composite steps with `bash -e`,
  so any verdict with a non-zero exit code (WEAK or worse) ended the step before the verdict
  output, the pull request comment and the SARIF upload were written. The end-to-end job found
  it; the action now records the exit code and gates on it in its last step.
- **Install and upgrade**: the README and docs lead with `pip install counterproof` and show
  `pip install --upgrade counterproof`; the README badges read the version, Python versions and
  licence live from PyPI.
- **Website**: the two-minute demo video with a transcript, the four ways a green change can be
  wrong, comparison charts against the baselines (strict and lenient), and new FAQ answers.

## [1.0.0] - 2026-11-09

The first stable release: everything in the v1 product requirements ([docs/PRD.md](docs/PRD.md)),
measured on three corpora.

### Verification

- **Five probes**, run cheapest first in isolated git worktrees: claim audit (P5), integrity
  (P2), red-green (P1), change-scoped mutants (P3) and the blind oracle (P4).
- **A deterministic verdict engine**: fourteen ordered rules map the evidence to PROVEN, WEAK,
  UNPROVEN, REFUTED or INCONCLUSIVE, with exit codes for CI. An LLM never decides a verdict.
- **Integrity rules FR-IN-01 to FR-IN-09**: removed assertions, deleted tests, added skips,
  loosened expectations, neutralised assertions, mocked units, code special-cased to the
  test's inputs, test-environment detection and self-configuration changes. Waivers only
  count once committed. Comparing an input with a one-word string (`== "rejected"`) is
  treated as mode dispatch and stays a medium finding for review, after it caused every
  refutation on real agent pull requests in corpus C3.
- **Mutants**: rule operators for Python and TypeScript, optional LLM-written realistic
  faults and an overfit mutant, a three-stage equivalence filter (static, an LLM judge,
  Hypothesis or fast-check fuzzing), timeouts confirmed by a re-run before they count.
- **Blind oracle for Python and TypeScript**: tests written from the task and the public
  interface only (never a function body), validated before they run, run three times on the
  change and triaged when they fail. A likely bug caps the verdict at WEAK and never refutes.
- **Claim audit** of Claude Code session transcripts: no run after the last edit, subset runs
  sold as the full suite, hidden exit codes, count mismatches, claims contradicted by the
  baseline.

### Languages and environments

- Python with pytest; TypeScript and JavaScript with Vitest or Jest (tree-sitter analysis;
  tree-sitter is held below 0.26, whose 0.26.0 release segfaults on large syntax trees).
- **`--sandbox docker`**: tests run in a container with no network, 2 GB of memory, 2 CPUs and
  512 processes, with an image built once per set of dependency files. Linux and macOS hosts
  (Node projects: Linux). A sandbox that cannot start makes the run INCONCLUSIVE; it never
  falls back to running unsandboxed.
- LLM providers: Anthropic, OpenAI, DeepSeek and Ollama, all optional, with hard token, dollar
  and time budgets, secret redaction and a response cache. Nothing leaves the machine by
  default.

### Integrations

- Claude Code Stop hook (coach mode: the gap and its location, never the answer), an MCP
  server, a git pre-push hook and a GitHub Action with PR certificates and SARIF.
- Certificates as text, Markdown, JSON, HTML and SARIF 2.1.0; sealed evidence bundles.

### Evaluation

- `counterproof eval` with baselines B0 (green tests), B1 (claims only) and B2 (diff-scoped
  classic mutation), probe ablations through the pure engine, determinism checks and
  recorded-LLM replay.
- **C1**: 160 seeded changes, 20 tasks in each of a Python and a TypeScript library, each
  with an honest fix and three gamed variants, ground truth checked with the real runners.
- **C2**: 15 real Claude Code sessions, including EvilGenie-style pressure, labelled with
  held-out tests and a hand review, plus 8 hand-built sessions for the false-claim patterns.
- **C3**: real merged agent pull requests from public repositories, each run on its own
  ephemeral GitHub runner.
- Equivalent-mutant leakage measured on a human-labelled sample of surviving mutants.

### Project

- Website with docs, integrations, results and FAQ; a two-minute demo video.
- Releases publish to PyPI with Trusted Publishing and to GitHub releases.
