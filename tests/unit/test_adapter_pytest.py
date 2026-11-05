"""JUnit parsing, FR-AD-04 failure kinds and the PytestAdapter's pure parts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from counterproof.adapters.base import classify_failure
from counterproof.adapters.junit import JUnitCase, junit_key, module_of, parse_junit
from counterproof.adapters.pytest_adapter import (
    INTERPRETER_ENV,
    PytestAdapter,
    _to_coverage_map,
    resolve_interpreter,
)
from counterproof.models import TestId

# Shape verified against pytest 9.2 output (junit_family=xunit2).
JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="2" skipped="1" tests="7">
<testcase classname="tests.test_a" name="test_one" time="0.017" />
<testcase classname="tests.test_a" name="test_two" time="0.004"><failure message="assert 2 == 3&#10; +  where 2 = f(5)">def test_two():
&gt;       assert f(5) == 3
E       assert 2 == 3

tests\\test_a.py:6: AssertionError</failure></testcase>
<testcase classname="tests.test_a.TestK" name="test_p[1-x]" time="0.002" />
<testcase classname="tests.test_a" name="test_err" time="0.001"><failure message="ValueError: boom">E       ValueError: boom</failure></testcase>
<testcase classname="tests.test_a" name="test_skip" time="0.001"><skipped type="pytest.skip" message="x">skipped</skipped></testcase>
<testcase classname="tests.test_a" name="test_env" time="0.1"><error message="failed on setup">E   ModuleNotFoundError: No module named 'requests'</error></testcase>
<testcase classname="" name="tests.test_broken" time="0.000"><error message="collection failure">E   ModuleNotFoundError: No module named 'nonexistent_mod'</error></testcase>
</testsuite></testsuites>
"""

REQUESTED = [
    "tests/test_a.py::test_one",
    "tests/test_a.py::test_two",
    "tests/test_a.py::TestK::test_p[1-x]",
    "tests/test_a.py::test_err",
    "tests/test_a.py::test_skip",
    "tests/test_a.py::test_env",
    "tests/test_broken.py::test_x",
    "tests/test_c.py::test_never_reported",
]


@pytest.fixture
def junit_file(tmp_path: Path) -> Path:
    path = tmp_path / "j.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


def test_junit_key_round_trips_nodeids() -> None:
    assert junit_key("tests/test_a.py::TestK::test_p[1-x]") == ("tests.test_a.TestK", "test_p[1-x]")
    assert junit_key("pkg/tests/test_b.py::test_q") == ("pkg.tests.test_b", "test_q")
    assert junit_key("test_top.py::test_r") == ("test_top", "test_r")
    assert module_of("tests/test_a.py") == "tests.test_a"


def test_parse_junit_outcomes(junit_file: Path) -> None:
    cases = {(c.classname, c.name): c for c in parse_junit(junit_file)}
    assert cases[("tests.test_a", "test_one")].outcome == "pass"
    assert cases[("tests.test_a", "test_two")].outcome == "fail"
    assert cases[("tests.test_a", "test_skip")].outcome == "skip"
    assert cases[("", "tests.test_broken")].is_collection_error
    assert cases[("tests.test_a", "test_one")].time_s == pytest.approx(0.017)


def test_parse_junit_missing_or_malformed_file(tmp_path: Path) -> None:
    assert parse_junit(tmp_path / "missing.xml") == []
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuites><testcase", encoding="utf-8")
    assert parse_junit(bad) == []


def _case(message: str = "", text: str = "", classname: str = "t", name: str = "n") -> JUnitCase:
    return JUnitCase(classname, name, "fail", message, text, 0.0)


@pytest.mark.parametrize(
    ("case", "kind"),
    [
        (_case("assert 2 == 3", "E   assert 2 == 3\nAssertionError"), "ASSERTION"),
        (_case("Failed: DID NOT RAISE <class 'ValueError'>"), "ASSERTION"),
        (_case("ValueError: boom", "E   ValueError: boom"), "EXCEPTION_BEHAVIOURAL"),
        (_case("", "ModuleNotFoundError: No module named 'requests'"), "ENV"),
        (_case("collection failure", "", classname="", name="tests.test_b"), "COLLECTION"),
        (_case("", "E   SyntaxError: invalid syntax"), "COLLECTION"),
    ],
)
def test_fr_ad_04_failure_kind(case: JUnitCase, kind: str) -> None:
    assert classify_failure(case) == kind


def test_fr_ad_04_timeout() -> None:
    assert classify_failure(_case("assert x"), timed_out=True) == "TIMEOUT"


def test_map_results(junit_file: Path, tmp_path: Path) -> None:
    adapter = PytestAdapter(tmp_path, interpreter=sys.executable)
    outcomes, failures = adapter._map_results(
        parse_junit(junit_file), REQUESTED, timed_out=False, fail_fast=False
    )
    assert outcomes == {
        "tests/test_a.py::test_one": "pass",
        "tests/test_a.py::test_two": "fail",
        "tests/test_a.py::TestK::test_p[1-x]": "pass",
        "tests/test_a.py::test_err": "fail",
        "tests/test_a.py::test_skip": "skip",
        "tests/test_a.py::test_env": "error",
        "tests/test_broken.py::test_x": "error",
        "tests/test_c.py::test_never_reported": "error",
    }
    assert failures["tests/test_a.py::test_two"].kind == "ASSERTION"
    assert failures["tests/test_a.py::test_err"].kind == "EXCEPTION_BEHAVIOURAL"
    assert failures["tests/test_a.py::test_env"].kind == "ENV"
    assert failures["tests/test_broken.py::test_x"].kind == "COLLECTION"


def test_map_results_fail_fast_and_timeout(junit_file: Path, tmp_path: Path) -> None:
    adapter = PytestAdapter(tmp_path, interpreter=sys.executable)
    outcomes, _ = adapter._map_results(
        parse_junit(junit_file), REQUESTED, timed_out=False, fail_fast=True
    )
    assert "tests/test_c.py::test_never_reported" not in outcomes  # not run, not an error
    outcomes, failures = adapter._map_results([], REQUESTED[:2], timed_out=True, fail_fast=False)
    assert set(outcomes.values()) == {"timeout"}
    assert {f.kind for f in failures.values()} == {"TIMEOUT"}


def test_interpreter_resolution_order(tmp_path: Path) -> None:
    exe = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    repo = tmp_path / "repo"
    venv = repo / ".venv" / exe
    other = tmp_path / "active" / exe
    for path in (venv, other):
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
    assert resolve_interpreter(repo, {INTERPRETER_ENV: "/opt/py"}) == "/opt/py"
    assert resolve_interpreter(repo, {"VIRTUAL_ENV": str(tmp_path / "active")}) == str(venv)
    venv.unlink()
    assert resolve_interpreter(repo, {"VIRTUAL_ENV": str(tmp_path / "active")}) == str(other)
    assert resolve_interpreter(repo, {}) == sys.executable


def test_coverage_map_paths_and_contexts(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    raw = {
        str(ws / "src" / "a.py"): {
            "1": [""],
            "2": ["tests/test_a.py::test_one|run", "tests/test_a.py::test_one|setup"],
            "3": ["tests/test_a.py::TestK::test_p[a|b]|run"],
        },
        str(tmp_path / "elsewhere.py"): {"1": ["tests/test_a.py::test_one|run"]},
    }
    cov = _to_coverage_map(raw, ws)
    assert cov == {
        ("src/a.py", 2): {"tests/test_a.py::test_one"},
        ("src/a.py", 3): {"tests/test_a.py::TestK::test_p[a|b]"},
    }


def test_fr_is_02_related_tests_by_name(tmp_path: Path) -> None:
    adapter = PytestAdapter(tmp_path, interpreter=sys.executable)
    tests = [
        TestId(adapter="pytest", file="tests/test_discounts.py", name="test_a"),
        TestId(adapter="pytest", file="tests/discounts_test.py", name="test_b"),
        TestId(adapter="pytest", file="tests/test_money.py", name="test_c"),
    ]
    related = adapter.related_tests(["src/ledger/discounts.py"], tests)
    assert [t.nodeid for t in related] == [
        "tests/test_discounts.py::test_a",
        "tests/discounts_test.py::test_b",
    ]


def test_detect(tmp_path: Path) -> None:
    adapter = PytestAdapter(tmp_path, interpreter=sys.executable)
    assert adapter.detect(tmp_path) == 0.0
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert adapter.detect(tmp_path) == 0.3
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n", encoding="utf-8")
    assert adapter.detect(tmp_path) == 0.8
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    assert adapter.detect(tmp_path) == 1.0
