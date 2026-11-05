"""FR-IS-01 to FR-IS-06: impact selection, flaky detection and timeouts."""

from __future__ import annotations

from pathlib import Path

import pytest

from counterproof.impact.baseline import analyse_baseline, mutant_timeout_s
from counterproof.impact.selector import changed_source_lines, select_tests
from counterproof.models import ChangedFile, ChangeSet, Hunk, RunResult, TestId, TestOutcome


def _run(outcomes: dict[str, TestOutcome], durations: dict[str, float] | None = None) -> RunResult:
    return RunResult(
        tests=outcomes,
        duration_s=1.0,
        command=["pytest"],
        exit_code=0,
        junit_sha256="0" * 64,
        test_durations=durations or {},
    )


A, B, C, D, E = (f"tests/test_x.py::test_{c}" for c in "abcde")


def test_fr_is_03_flaky_when_outcomes_differ() -> None:
    runs = [
        _run({A: "pass", B: "pass"}),
        _run({A: "fail", B: "pass"}),
        _run({A: "pass", B: "pass"}),
    ]
    report = analyse_baseline(runs, [A, B], max_ratio=0.6)
    assert report.flaky == [A]
    assert report.stable == [B]
    assert report.status == "ok"
    assert report.outcomes[A] == ["pass", "fail", "pass"]


def test_fr_is_03_consistent_skip_is_not_flaky_or_failing() -> None:
    runs = [_run({A: "skip"})] * 3
    report = analyse_baseline(runs, [A])
    assert (report.flaky, report.failing, report.status) == ([], [], "ok")


def test_fr_is_03_known_flaky_excluded_and_not_counted() -> None:
    runs = [_run({A: "pass", B: "pass"}), _run({A: "fail", B: "pass"})]
    report = analyse_baseline(runs, [A, B], known_flaky=[A, "tests/other.py::t"], max_ratio=0.0)
    assert report.known_flaky == [A]
    assert report.flaky == []
    assert report.status == "ok"
    assert report.stable == [B]


@pytest.mark.parametrize(("flaky_count", "status"), [(1, "ok"), (2, "flaky_suite")])
def test_fr_is_04_flaky_ratio_threshold_is_strictly_greater(flaky_count: int, status: str) -> None:
    tests = [A, B, C, D, E]
    first = dict.fromkeys(tests, "pass")
    second = {t: ("fail" if i < flaky_count else "pass") for i, t in enumerate(tests)}
    report = analyse_baseline([_run(first), _run(second)], tests, max_ratio=0.2)  # type: ignore[arg-type]
    assert report.flaky_ratio == pytest.approx(flaky_count / 5)
    assert report.status == status


def test_fr_is_04_flaky_suite_wins_over_head_red() -> None:
    """Section 8 order: R-INC-FLAKY (rule 2) is checked before R-INC-HEADRED (rule 4)."""
    runs = [_run({A: "pass", B: "fail"}), _run({A: "fail", B: "fail"})]
    report = analyse_baseline(runs, [A, B], max_ratio=0.2)
    assert report.failing == [B]
    assert report.status == "flaky_suite"


def test_fr_is_05_head_red_on_consistent_failure() -> None:
    runs = [_run({A: "pass", B: "error"})] * 3
    report = analyse_baseline(runs, [A, B])
    assert (report.status, report.failing) == ("head_red", [B])


def test_fr_is_05_missing_result_counts_as_error() -> None:
    runs = [_run({A: "pass"})] * 3
    report = analyse_baseline(runs, [A, B])
    assert report.failing == [B]


def test_no_selected_tests() -> None:
    assert analyse_baseline([_run({})], []).status == "no_tests"


def test_fr_is_06_durations_and_mutant_timeout() -> None:
    runs = [_run({A: "pass", B: "pass"}, {A: 0.5, B: 2.0}), _run({A: "pass", B: "pass"}, {A: 1.5})]
    report = analyse_baseline(runs, [A, B])
    assert report.durations == {A: 1.5, B: 2.0}
    assert mutant_timeout_s(report, [A, B]) == pytest.approx(3 * 3.5 + 5)
    assert mutant_timeout_s(report, []) == 5.0


def _change(files: list[ChangedFile], hunks: list[Hunk]) -> ChangeSet:
    return ChangeSet(
        repo_root=Path("/r"),
        base_sha="a" * 40,
        head_ref="WORKTREE",
        head_snapshot_sha="b" * 40,
        files=files,
        hunks=hunks,
        intent="bugfix",
        intent_source="rule",
    )


def _tid(nodeid: str) -> TestId:
    file, _, name = nodeid.partition("::")
    return TestId(adapter="pytest", file=file, name=name)


ALL = [
    _tid("tests/test_disc.py::test_cap"),
    _tid("tests/test_disc.py::test_basic"),
    _tid("tests/test_inv.py::test_total"),
    _tid("tests/test_inv.py::test_subtotal"),
    _tid("tests/test_tax.py::test_tax"),
]
CHANGE = _change(
    [
        ChangedFile(path="src/disc.py", role="source", status="modified"),
        ChangedFile(path="tests/test_disc.py", role="test", status="modified"),
        ChangedFile(path="src/gone.py", role="source", status="deleted"),
    ],
    [
        Hunk(path="src/disc.py", old_start=17, old_len=0, new_start=18, new_len=2),
        Hunk(path="src/disc.py", old_start=30, old_len=2, new_start=31, new_len=0),
        Hunk(path="src/gone.py", old_start=1, old_len=5, new_start=0, new_len=0),
    ],
)
COVERAGE = {
    ("src/disc.py", 18): {"tests/test_disc.py::test_basic", "tests/test_inv.py::test_total"},
    ("src/disc.py", 32): {"tests/test_tax.py::test_tax"},
    ("src/disc.py", 40): {"tests/test_inv.py::test_subtotal"},
    ("src/gone.py", 1): {"tests/test_inv.py::test_subtotal"},
}


def test_changed_source_lines_skip_deleted_files() -> None:
    assert changed_source_lines(CHANGE) == {"src/disc.py": {18, 19, 31, 32}}


def test_fr_is_01_union_of_covering_changed_and_related() -> None:
    selection = select_tests(
        CHANGE, ALL, COVERAGE, [_tid("tests/test_tax.py::test_tax")], max_tests=400
    )
    assert selection.covering == [
        "tests/test_disc.py::test_basic",
        "tests/test_inv.py::test_total",
        "tests/test_tax.py::test_tax",
    ]
    assert selection.in_changed_test_files == [
        "tests/test_disc.py::test_basic",
        "tests/test_disc.py::test_cap",
    ]
    assert "tests/test_inv.py::test_subtotal" not in selection.tests
    assert selection.tests == sorted(
        {
            "tests/test_disc.py::test_basic",
            "tests/test_disc.py::test_cap",
            "tests/test_inv.py::test_total",
            "tests/test_tax.py::test_tax",
        }
    )
    assert selection.coverage_available and not selection.capped


def test_fr_is_02_without_coverage_uses_related_tests() -> None:
    selection = select_tests(
        CHANGE, ALL, None, [_tid("tests/test_inv.py::test_total")], max_tests=400
    )
    assert not selection.coverage_available
    assert selection.covering == []
    assert selection.tests == [
        "tests/test_disc.py::test_basic",
        "tests/test_disc.py::test_cap",
        "tests/test_inv.py::test_total",
    ]


def test_fr_is_01_cap_falls_back_to_whole_files() -> None:
    selection = select_tests(CHANGE, ALL, COVERAGE, [], max_tests=2)
    assert selection.capped
    assert selection.file_targets == [
        "tests/test_disc.py",
        "tests/test_inv.py",
        "tests/test_tax.py",
    ]
    assert "tests/test_inv.py::test_subtotal" in selection.tests


def test_stale_coverage_contexts_are_ignored() -> None:
    coverage = {("src/disc.py", 18): {"tests/test_old.py::test_removed"}}
    selection = select_tests(CHANGE, ALL, coverage, [], max_tests=400)
    assert "tests/test_old.py::test_removed" not in selection.tests
