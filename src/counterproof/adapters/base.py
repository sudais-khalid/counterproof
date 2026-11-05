"""Common adapter interface (PRD section 7.3) and shared failure classification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from counterproof.adapters.junit import JUnitCase
from counterproof.models import FailureKind, RunResult, TestId
from counterproof.workspace.manager import Workspace

FileLine = tuple[str, int]
"""Workspace-relative POSIX path and 1-based line number."""
CoverageMap = dict[FileLine, set[str]]


@dataclass(frozen=True)
class PrepResult:
    ok: bool
    message: str = ""
    interpreter: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()


class CollectionError(Exception):
    """Test discovery failed (syntax error, import error at collection time)."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class Adapter(Protocol):
    name: str

    def detect(self, repo: Path) -> float: ...

    def is_test_file(self, path: str) -> bool: ...

    def prepare(self, ws: Workspace) -> PrepResult: ...

    def list_tests(self, ws: Workspace, files: list[str] | None = None) -> list[TestId]: ...

    def run(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        fail_fast: bool,
        junit_path: Path,
        coverage_file: Path | None = None,
    ) -> RunResult: ...

    def coverage_map(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        junit_path: Path,
        coverage_file: Path,
    ) -> CoverageMap | None: ...

    def related_tests(self, src_files: list[str], all_tests: list[TestId]) -> list[TestId]: ...

    def failure_kind(self, case: JUnitCase) -> FailureKind: ...


_COLLECTION_HINTS: Final = re.compile(
    r"(collection failure|ERROR collecting|SyntaxError|IndentationError|"
    r"ImportError while importing test module)"
)
_ENV_HINTS: Final = re.compile(
    r"(ModuleNotFoundError: No module named|ConnectionRefusedError|ConnectionError|"
    r"socket\.gaierror|Temporary failure in name resolution)"
)
_ASSERTION_HINTS: Final = re.compile(
    r"(AssertionError|^assert |\nE\s+assert |DID NOT RAISE|Failed: DID NOT|"
    r"AssertionError:|expect\(|toBe|toEqual)"
)


def classify_failure(case: JUnitCase, *, timed_out: bool = False) -> FailureKind:
    """FR-AD-04, change-agnostic part.

    ``API_ABSENT`` needs to know which symbols the change added, so the red-green probe (M2)
    refines ``EXCEPTION_BEHAVIOURAL`` and ``ENV`` results into ``API_ABSENT`` where it applies.
    """
    if timed_out:
        return "TIMEOUT"
    blob = f"{case.message}\n{case.text}"
    if case.is_collection_error or _COLLECTION_HINTS.search(blob):
        return "COLLECTION"
    if _ASSERTION_HINTS.search(blob):
        return "ASSERTION"
    if _ENV_HINTS.search(blob):
        return "ENV"
    return "EXCEPTION_BEHAVIOURAL"
