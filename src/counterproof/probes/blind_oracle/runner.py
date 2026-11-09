"""OracleRunner (FR-BO-03, FR-BO-04): oracle tests on head (3 runs), failing ones on base.

The oracle file is placed next to the style example inside the run's worktrees (so the
repository's conftest files apply) and removed after each use. Worktrees live in the run
directory, so the user's repository never sees an oracle test (FR-BO-06).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from counterproof.adapters.base import Adapter
from counterproof.models import FailureInfo, FailureKind, RunResult, TestOutcome
from counterproof.probes.blind_oracle.writer import ORACLE_FILENAME, OracleCase, OracleSuite
from counterproof.workspace.manager import Workspace

HEAD_RUNS: Final = 3
DISCARD_KINDS: Final[frozenset[FailureKind]] = frozenset({"COLLECTION", "ENV", "TIMEOUT"})
OracleStatus = Literal["agree", "failing", "discarded"]


@dataclass(frozen=True)
class OracleResult:
    case: OracleCase
    nodeid: str
    status: OracleStatus
    reason: str = ""
    head_outcomes: tuple[TestOutcome, ...] = ()
    failure: FailureInfo | None = None
    base_outcome: TestOutcome | None = None
    base_failure: FailureInfo | None = None


def oracle_path(ws: Workspace, test_dir: str, filename: str = ORACLE_FILENAME) -> str:
    """A repo-relative path for the oracle file that does not collide with a real one."""
    folder = PurePosixPath(test_dir) if test_dir not in {"", "."} else PurePosixPath()
    stem, dot, suffix = filename.partition(".")
    for n in range(1, 100):
        name = filename if n == 1 else f"{stem}_{n}{dot}{suffix}"
        rel = (folder / name).as_posix()
        if not (ws.root / rel).exists():
            return rel
    raise RuntimeError("no free name for the oracle test file")


def _run_in(
    adapter: Adapter,
    ws: Workspace,
    rel: str,
    text: str,
    nodeids: list[str],
    *,
    runs: int,
    junit: Callable[[int], Path],
    timeout_s: float,
) -> list[RunResult]:
    target = ws.root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    try:
        return [
            adapter.run(ws, nodeids, timeout_s=timeout_s, fail_fast=False, junit_path=junit(i))
            for i in range(1, runs + 1)
        ]
    finally:
        target.unlink(missing_ok=True)


def _classify(nodeid: str, results: Sequence[RunResult]) -> tuple[OracleStatus, str]:
    outcomes = [r.tests.get(nodeid, "error") for r in results]
    failures = [r.failures[nodeid] for r in results if nodeid in r.failures]
    kinds = {f.kind for f in failures}
    if kinds & DISCARD_KINDS:
        return "discarded", f"{sorted(kinds & DISCARD_KINDS)[0].lower()} error"
    if "API_ABSENT" in kinds:
        return "discarded", "uses a name the code does not define"
    if "skip" in outcomes:
        return "discarded", "skipped"
    if all(o == "pass" for o in outcomes):
        return "agree", ""
    if any(o == "pass" for o in outcomes):
        return "discarded", "flaky on head"
    return "failing", ""


def run_oracle(
    adapter: Adapter,
    *,
    head: Workspace,
    base: Workspace,
    suite: OracleSuite,
    test_dir: str,
    header: str,
    junit_path: Callable[[str], Path],
    timeout_s: float,
    filename: str = ORACLE_FILENAME,
) -> tuple[str, list[OracleResult]]:
    """``(oracle file path, results)``; the path is relative to the repository root."""
    rel = oracle_path(head, test_dir, filename)
    text = suite.text(header)
    nodeids = {c.name: f"{rel}::{c.name}" for c in suite.cases}
    head_runs = _run_in(
        adapter,
        head,
        rel,
        text,
        list(nodeids.values()),
        runs=HEAD_RUNS,
        junit=lambda i: junit_path(f"head-{i}"),
        timeout_s=timeout_s,
    )
    results: list[OracleResult] = []
    for case in suite.cases:
        nodeid = nodeids[case.name]
        status, reason = _classify(nodeid, head_runs)
        failure = next((r.failures[nodeid] for r in head_runs if nodeid in r.failures), None)
        results.append(
            OracleResult(
                case=case,
                nodeid=nodeid,
                status=status,
                reason=reason,
                head_outcomes=tuple(r.tests.get(nodeid, "error") for r in head_runs),
                failure=failure if status != "agree" else None,
            )
        )

    failing = [r for r in results if r.status == "failing"]
    if not failing:
        return rel, results
    base_rel = rel if not (base.root / rel).exists() else oracle_path(base, test_dir, filename)
    base_ids = [f"{base_rel}::{r.case.name}" for r in failing]
    (base_run,) = _run_in(
        adapter,
        base,
        base_rel,
        text,
        base_ids,
        runs=1,
        junit=lambda _i: junit_path("base"),
        timeout_s=timeout_s,
    )
    by_name = {r.case.name: r for r in failing}
    updated: dict[str, OracleResult] = {}
    for nodeid in base_ids:
        name = nodeid.rsplit("::", 1)[-1]
        old = by_name[name]
        updated[name] = OracleResult(
            case=old.case,
            nodeid=old.nodeid,
            status=old.status,
            reason=old.reason,
            head_outcomes=old.head_outcomes,
            failure=old.failure,
            base_outcome=base_run.tests.get(nodeid, "error"),
            base_failure=base_run.failures.get(nodeid),
        )
    return rel, [updated.get(r.case.name, r) for r in results]
