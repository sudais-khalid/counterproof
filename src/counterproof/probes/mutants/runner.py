"""Mutant execution (FR-MU-10, FR-MU-11) on a pool of head worktrees (FR-WS-03).

Each worker owns one head worktree. A mutant is written into its file, the covering tests
run fail-fast with the per-mutant timeout, and the file is reset with ``git checkout --``
before the worker takes the next mutant. Survivors are re-run once (not fail-fast) to rule
out flakiness.
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from counterproof import git
from counterproof.adapters.base import Adapter
from counterproof.log import get_logger
from counterproof.models import Mutant, MutantStatus, RunResult
from counterproof.probes.mutants.apply import apply_mutant
from counterproof.workspace.manager import Workspace

log = get_logger("mutants")
FAILING: Final = frozenset({"fail", "error"})
TIMEOUT_RETRY_FACTOR: Final = 2.0


@dataclass(frozen=True)
class Verdict:
    status: MutantStatus
    killed_by: list[str]
    note: str = ""


def classify_run(result: RunResult) -> Verdict:
    """FR-MU-10: KILLED / SURVIVED / TIMEOUT / ERROR, plus ``invalid`` for import failures."""
    outcomes = result.tests
    if not outcomes:
        return Verdict("error", [], "no test result reported")
    timed_out = sorted(t for t, o in outcomes.items() if o == "timeout")
    if timed_out:
        return Verdict("timeout", timed_out)
    failing = sorted(t for t, o in outcomes.items() if o in FAILING)
    if failing:
        kinds = {result.failures[t].kind for t in failing if t in result.failures}
        if kinds == {"COLLECTION"} and not any(o == "pass" for o in outcomes.values()):
            # FR-MU-06: a mutant that breaks import or collection is invalid, not killed.
            return Verdict("invalid", [], "fails to import or collect")
        if kinds == {"ENV"}:
            return Verdict("error", [], "environment failure")
        return Verdict("killed", failing)
    return Verdict("survived", [])


@dataclass(frozen=True)
class MutantJob:
    mutant: Mutant
    tests: list[str]
    timeout_s: float


def run_mutants(
    jobs: Sequence[MutantJob],
    *,
    adapter: Adapter,
    workers: Sequence[Workspace],
    head_text: Callable[[str], str],
    junit_path: Callable[[str], Path],
) -> list[Mutant]:
    """Run every job; returns the mutants with status and ``killed_by`` filled in."""
    if not jobs:
        return []
    free: queue.Queue[Workspace] = queue.Queue()
    for ws in workers:
        free.put(ws)

    def attempt(ws: Workspace, job: MutantJob, name: str, *, fail_fast: bool) -> Verdict:
        """One classified run. A timeout is ambiguous (a hanging mutant, or a busy machine),
        so it is confirmed once with twice the time before it counts (FR-MU-10)."""
        verdict = classify_run(
            adapter.run(
                ws,
                job.tests,
                timeout_s=job.timeout_s,
                fail_fast=fail_fast,
                junit_path=junit_path(name),
            )
        )
        if verdict.status != "timeout":
            return verdict
        return classify_run(
            adapter.run(
                ws,
                job.tests,
                timeout_s=job.timeout_s * TIMEOUT_RETRY_FACTOR,
                fail_fast=fail_fast,
                junit_path=junit_path(f"{name}-retry"),
            )
        )

    def run_one(job: MutantJob) -> Mutant:
        ws = free.get()
        mutant = job.mutant
        target = ws.root / mutant.file
        try:
            target.write_bytes(apply_mutant(head_text(mutant.file), mutant).encode("utf-8"))
            if not job.tests:
                return mutant.model_copy(update={"status": "error"})
            verdict = attempt(ws, job, mutant.id, fail_fast=True)
            if verdict.status == "survived":
                # FR-MU-11: re-run once; a survivor that now dies was flaky, not proven.
                again = attempt(ws, job, f"{mutant.id}-rerun", fail_fast=False)
                if again.status == "timeout":
                    verdict = Verdict("error", [], "re-run timed out twice")
                elif again.status != "survived":
                    verdict = Verdict("error", [], "flaky: survived once, killed on re-run")
            log.info("mutant %s: %s %s", mutant.id, verdict.status, verdict.note)
            return mutant.model_copy(
                update={"status": verdict.status, "killed_by": verdict.killed_by}
            )
        finally:
            git.run_git(["checkout", "--", mutant.file], ws.root)  # FR-WS-03 reset
            free.put(ws)

    with ThreadPoolExecutor(max_workers=len(workers), thread_name_prefix="mutant") as pool:
        return list(pool.map(run_one, jobs))
