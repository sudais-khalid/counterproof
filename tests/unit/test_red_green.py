"""P1 red-green logic (FR-RG-01 to FR-RG-07) and FR-AD-04 API_ABSENT, with a fake adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from counterproof.adapters.base import NOT_REPORTED, refine_api_absent
from counterproof.models import (
    BaselineReport,
    ChangedFile,
    ChangeSet,
    FailureInfo,
    FailureKind,
    RunResult,
    Selection,
    TestOutcome,
)
from counterproof.probes.red_green import run_red_green, select_candidates, transplant_paths
from counterproof.workspace.manager import Workspace

ADDED = {"split", "Ledger", "percent_discount(cap)"}
MODULES = {"ledger.fx"}


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("COLLECTION", "ImportError: cannot import name 'split' from 'ledger.money'", "API_ABSENT"),
        (
            "EXCEPTION_BEHAVIOURAL",
            "AttributeError: 'Money' object has no attribute 'split'",
            "API_ABSENT",
        ),
        (
            "EXCEPTION_BEHAVIOURAL",
            "TypeError: percent_discount() got an unexpected keyword argument 'cap'",
            "API_ABSENT",
        ),
        (
            "EXCEPTION_BEHAVIOURAL",
            "TypeError: percent_discount() takes 2 positional arguments but 3 were given",
            "API_ABSENT",
        ),
        ("ENV", "ModuleNotFoundError: No module named 'ledger.fx'", "API_ABSENT"),
        ("ENV", "ModuleNotFoundError: No module named 'requests'", "ENV"),
        (
            "EXCEPTION_BEHAVIOURAL",
            "AttributeError: 'Money' object has no attribute 'amount_'",
            "EXCEPTION_BEHAVIOURAL",
        ),
        (
            "EXCEPTION_BEHAVIOURAL",
            "ValueError: percent must be between 0 and 100",
            "EXCEPTION_BEHAVIOURAL",
        ),
        ("ASSERTION", "AssertionError: 'split' != 'join'", "ASSERTION"),
    ],
)
def test_fr_ad_04_api_absent(kind: FailureKind, text: str, expected: FailureKind) -> None:
    assert refine_api_absent(kind, text, ADDED, MODULES) == expected


A, B, C = "tests/test_x.py::test_a", "tests/test_x.py::test_b", "tests/test_y.py::test_c"
P1, P2 = "tests/test_x.py::test_p[1]", "tests/test_x.py::test_p[2]"


def _baseline(green: list[str], flaky: list[str] | None = None) -> BaselineReport:
    tests = green + (flaky or [])
    outcomes: dict[str, list[TestOutcome]] = {t: ["pass"] * 3 for t in green}
    for t in flaky or []:
        outcomes[t] = ["pass", "fail", "pass"]
    return BaselineReport(runs=3, status="ok", selected=tests, flaky=flaky or [], outcomes=outcomes)


def test_fr_rg_01_changed_tests_first_then_covering() -> None:
    baseline = _baseline([A, B, C, P1, P2])
    selection = Selection(tests=[A, B, C, P1, P2], covering=[C])
    collected = [A, B, C, P1, P2]
    assert select_candidates(["tests/test_x.py::test_p"], collected, selection, baseline) == (
        [P1, P2],
        "changed_tests",
    )
    assert select_candidates([], collected, selection, baseline) == ([C], "covering_tests")
    assert select_candidates([], collected, Selection(tests=[A]), baseline) == ([], "none")


def test_fr_rg_01_flaky_and_red_tests_are_not_candidates() -> None:
    baseline = _baseline([A], flaky=[B])
    assert select_candidates([A, B], [A, B], Selection(tests=[A, B]), baseline) == (
        [A],
        "changed_tests",
    )


def test_fr_rg_02_transplants_tests_and_helpers_never_source() -> None:
    change = ChangeSet(
        repo_root=Path("/r"),
        base_sha="a" * 40,
        head_ref="WORKTREE",
        head_snapshot_sha="b" * 40,
        intent="bugfix",
        intent_source="rule",
        files=[
            ChangedFile(path="src/ledger/a.py", role="source", status="modified"),
            ChangedFile(path="tests/test_a.py", role="test", status="modified"),
            ChangedFile(path="tests/conftest.py", role="test", status="added"),
            ChangedFile(path="tests/data/cases.json", role="config", status="added"),
            ChangedFile(path="tests/test_gone.py", role="test", status="deleted"),
        ],
    )
    assert transplant_paths(change) == [
        "tests/conftest.py",
        "tests/data/cases.json",
        "tests/test_a.py",
    ]


class FakeAdapter:
    """Returns scripted base results; records the tests of each run."""

    name = "fake"

    def __init__(self, *runs: dict[str, tuple[TestOutcome, FailureKind | None, str]]) -> None:
        self.runs = list(runs)
        self.calls: list[list[str]] = []

    def run(self, ws: Workspace, tests: list[str], **_: Any) -> RunResult:
        self.calls.append(list(tests))
        scripted = self.runs.pop(0)
        outcomes: dict[str, TestOutcome] = {t: scripted[t][0] for t in tests if t in scripted}
        failures = {
            t: FailureInfo(kind=kind, message=msg)
            for t, (_, kind, msg) in scripted.items()
            if t in tests and kind is not None
        }
        return RunResult(
            tests=outcomes,
            failures=failures,
            duration_s=0.1,
            command=["fake"],
            exit_code=1,
            junit_sha256="0" * 64,
        )


def _run(adapter: FakeAdapter, tmp_path: Path, changed: list[str], green: list[str]) -> Any:
    (tmp_path / "head").mkdir(exist_ok=True)
    (tmp_path / "base").mkdir(exist_ok=True)
    change = ChangeSet(
        repo_root=tmp_path,
        base_sha="a" * 40,
        head_ref="WORKTREE",
        head_snapshot_sha="b" * 40,
        intent="bugfix",
        intent_source="rule",
    )
    return run_red_green(
        change=change,
        adapter=adapter,  # type: ignore[arg-type]
        head=Workspace("head", tmp_path / "head", "b" * 40),
        base=Workspace("base", tmp_path / "base", "a" * 40),
        changed_tests=changed,
        collected=green,
        selection=Selection(tests=green),
        baseline=_baseline(green),
        added_symbols=ADDED,
        new_modules=MODULES,
        junit_path=lambda name: tmp_path / f"{name}.xml",
        timeout_s=60,
    )


def test_fr_rg_04_strong_on_behavioural_failure(tmp_path: Path) -> None:
    adapter = FakeAdapter({A: ("fail", "ASSERTION", "assert 50 == 30"), B: ("pass", None, "")})
    out = _run(adapter, tmp_path, [A, B], [A, B])
    assert out.result.signals == ["RG_STRONG"]
    assert out.result.metrics["strong"] == 1.0
    assert [c.base_kind for c in out.cases] == ["ASSERTION", None]


def test_fr_rg_05_api_only(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        {
            A: (
                "fail",
                "EXCEPTION_BEHAVIOURAL",
                "AttributeError: 'Money' object has no attribute 'split'",
            )
        }
    )
    out = _run(adapter, tmp_path, [A], [A])
    assert out.result.signals == ["RG_API_ONLY"]
    assert out.cases[0].base_kind == "API_ABSENT"


def test_fr_rg_04_strong_wins_over_api_absent(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        {
            A: (
                "fail",
                "EXCEPTION_BEHAVIOURAL",
                "TypeError: percent_discount() got an unexpected keyword argument 'cap'",
            ),
            B: ("fail", "ASSERTION", "assert 1 == 2"),
        }
    )
    assert _run(adapter, tmp_path, [A, B], [A, B]).result.signals == ["RG_STRONG"]


def test_fr_rg_06_pass_both(tmp_path: Path) -> None:
    adapter = FakeAdapter({A: ("pass", None, ""), B: ("skip", None, "")})
    out = _run(adapter, tmp_path, [A, B], [A, B])
    assert out.result.signals == ["RG_PASS_BOTH"]
    assert out.result.status == "ok"


def test_fr_rg_07_collection_failure_retried_without_the_file(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        {
            A: ("error", "COLLECTION", "SyntaxError in helper"),
            B: ("error", "COLLECTION", "SyntaxError in helper"),
            C: ("error", "COLLECTION", NOT_REPORTED),  # aborted session
        },
        {C: ("fail", "ASSERTION", "assert 1 == 2")},
    )
    out = _run(adapter, tmp_path, [A, B, C], [A, B, C])
    assert adapter.calls == [[A, B, C], [C]]
    assert out.result.signals == ["RG_STRONG", "RG_COLLECTION"]
    assert out.result.metrics["collection_errors"] == 2.0


def test_fr_rg_07_persistent_collection_failure_is_inconclusive(tmp_path: Path) -> None:
    adapter = FakeAdapter({A: ("error", "COLLECTION", "SyntaxError")})
    out = _run(adapter, tmp_path, [A], [A])
    assert adapter.calls == [[A]]  # nothing left to retry with
    assert (out.result.status, out.result.signals) == ("inconclusive", ["RG_COLLECTION"])


def test_env_or_timeout_only_failures_are_unclear(tmp_path: Path) -> None:
    adapter = FakeAdapter({A: ("timeout", "TIMEOUT", "run timed out")})
    out = _run(adapter, tmp_path, [A], [A])
    assert (out.result.status, out.result.signals) == ("inconclusive", ["RG_UNCLEAR"])


def test_no_candidates(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    out = _run(adapter, tmp_path, [], [])
    assert (out.result.status, out.result.signals) == ("inconclusive", ["RG_NO_CANDIDATES"])
    assert adapter.calls == []
