"""P1 Red-green probe (PRD section 7.5, FR-RG-01 to FR-RG-07).

The change's tests must fail on the old code *for a behavioural reason* and pass on the new
code. Head results come from the baseline (only tests that passed in every head run are
candidates); base results come from running the transplanted tests in the base worktree.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from counterproof.adapters.base import NOT_REPORTED, Adapter, refine_api_absent
from counterproof.change.classify import TEST_DIRS
from counterproof.models import (
    BaselineReport,
    ChangeSet,
    Cost,
    FailureKind,
    ProbeResult,
    ProbeStatus,
    RedGreenCase,
    RunResult,
    Selection,
)
from counterproof.workspace.manager import Workspace

BEHAVIOURAL: Final[frozenset[FailureKind]] = frozenset({"ASSERTION", "EXCEPTION_BEHAVIOURAL"})
FAILED: Final = frozenset({"fail", "error", "timeout"})

RG_STRONG: Final = "RG_STRONG"
RG_API_ONLY: Final = "RG_API_ONLY"
RG_PASS_BOTH: Final = "RG_PASS_BOTH"  # noqa: S105 - a signal name, not a password
RG_UNCLEAR: Final = "RG_UNCLEAR"
RG_NO_CANDIDATES: Final = "RG_NO_CANDIDATES"
RG_COLLECTION: Final = "RG_COLLECTION"


@dataclass
class RedGreenOutcome:
    result: ProbeResult
    cases: list[RedGreenCase] = field(default_factory=list)
    transplanted: list[str] = field(default_factory=list)
    candidate_source: str = "none"


def select_candidates(
    changed_tests: Sequence[str],
    collected: Sequence[str],
    selection: Selection,
    baseline: BaselineReport,
) -> tuple[list[str], str]:
    """FR-RG-01: added or modified tests; if none, covering tests of the changed lines.

    Only tests that passed in every baseline run qualify (they must be green on head), and
    flaky or known-flaky tests are excluded (FR-IS-03).
    """
    green = {
        t
        for t in baseline.stable
        if baseline.outcomes.get(t) and all(o == "pass" for o in baseline.outcomes[t])
    }

    def expand(test_id: str) -> list[str]:
        # A changed parametrised function maps to all of its collected cases.
        return [c for c in collected if c == test_id or c.startswith(test_id + "[")]

    changed = sorted({c for t in changed_tests for c in expand(t)} & green)
    if changed:
        return changed, "changed_tests"
    covering = sorted(set(selection.covering) & green)
    return covering, ("covering_tests" if covering else "none")


def transplant_paths(change: ChangeSet) -> list[str]:
    """FR-RG-02: changed test files and test helpers/fixtures, never source files."""
    paths = []
    for f in change.files:
        if f.status == "deleted":
            continue
        in_test_dir = any(part in TEST_DIRS for part in PurePosixPath(f.path).parts[:-1])
        if f.role == "test" or (in_test_dir and f.role != "source"):
            paths.append(f.path)
    return sorted(paths)


def _transplant(paths: Sequence[str], head: Workspace, base: Workspace) -> list[str]:
    copied = []
    for rel in paths:
        source = head.root / rel
        if not source.is_file():
            continue
        target = base.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(rel)
    return copied


def _cases(
    run: RunResult,
    tests: Sequence[str],
    added_symbols: set[str],
    new_modules: set[str],
) -> dict[str, RedGreenCase]:
    cases: dict[str, RedGreenCase] = {}
    for t in tests:
        outcome = run.tests.get(t)
        if outcome is None:
            cases[t] = RedGreenCase(nodeid=t, base_outcome="not_run")
            continue
        failure = run.failures.get(t)
        kind: FailureKind | None = None
        message = ""
        if outcome in FAILED and failure is not None:
            text = f"{failure.message}\n{failure.traceback_excerpt}"
            kind = refine_api_absent(failure.kind, text, added_symbols, new_modules)
            message = failure.message[:300]
        cases[t] = RedGreenCase(nodeid=t, base_outcome=outcome, base_kind=kind, message=message)
    return cases


def run_red_green(
    *,
    change: ChangeSet,
    adapter: Adapter,
    head: Workspace,
    base: Workspace,
    changed_tests: Sequence[str],
    collected: Sequence[str],
    selection: Selection,
    baseline: BaselineReport,
    added_symbols: set[str],
    new_modules: set[str],
    junit_path: Callable[[str], Path],
    timeout_s: float,
) -> RedGreenOutcome:
    started = time.monotonic()
    candidates, source = select_candidates(changed_tests, collected, selection, baseline)
    if not candidates:
        return RedGreenOutcome(
            ProbeResult(
                probe="red_green",
                status="inconclusive",
                signals=[RG_NO_CANDIDATES],
                metrics={"candidates": 0.0},
                cost=Cost(seconds=round(time.monotonic() - started, 6)),
            )
        )

    transplanted = _transplant(transplant_paths(change), head, base)
    first = adapter.run(
        base, candidates, timeout_s=timeout_s, fail_fast=False, junit_path=junit_path("base-1")
    )
    cases = _cases(first, candidates, added_symbols, new_modules)

    # FR-RG-07: retry once without the files that failed to collect. Tests that merely went
    # unreported because pytest aborted the session are retried, not excluded.
    broken = {
        c.nodeid.partition("::")[0]
        for c in cases.values()
        if c.base_kind == "COLLECTION" and c.message != NOT_REPORTED
    }
    retry = [t for t in candidates if t.partition("::")[0] not in broken]
    if broken and retry:
        second = adapter.run(
            base, retry, timeout_s=timeout_s, fail_fast=False, junit_path=junit_path("base-2")
        )
        cases.update(_cases(second, retry, added_symbols, new_modules))

    ordered = [cases[t] for t in candidates]
    usable = [c for c in ordered if c.base_kind != "COLLECTION"]
    strong = [c for c in usable if c.base_outcome in FAILED and c.base_kind in BEHAVIOURAL]
    api_only = [c for c in usable if c.base_kind == "API_ABSENT"]
    passed = [c for c in usable if c.base_outcome in {"pass", "skip"}]
    # Any candidate still uncollectable after the retry adds no evidence either way.
    lost = len(ordered) - len(usable)

    status: ProbeStatus = "ok"
    if strong:
        signal = RG_STRONG  # FR-RG-04
    elif api_only:
        signal = RG_API_ONLY  # FR-RG-05
    elif usable and len(passed) == len(usable):
        signal = RG_PASS_BOTH  # FR-RG-06
    elif not usable:
        signal, status = RG_COLLECTION, "inconclusive"  # FR-RG-07: persistent failure
    else:
        signal, status = RG_UNCLEAR, "inconclusive"  # only ENV/TIMEOUT failures on base

    signals = [signal]
    if lost and signal != RG_COLLECTION:
        signals.append(RG_COLLECTION)  # noted on the certificate: some tests gave no evidence
    return RedGreenOutcome(
        ProbeResult(
            probe="red_green",
            status=status,
            signals=signals,
            metrics={
                "candidates": float(len(candidates)),
                "failed_on_base": float(sum(1 for c in usable if c.base_outcome in FAILED)),
                "strong": float(len(strong)),
                "api_absent": float(len(api_only)),
                "passed_on_base": float(len(passed)),
                "collection_errors": float(lost),
            },
            cost=Cost(seconds=round(time.monotonic() - started, 6)),
        ),
        cases=ordered,
        transplanted=transplanted,
        candidate_source=source,
    )
