"""P2 integrity rules FR-IN-01 to FR-IN-09, waivers, and the shared libcst analysis."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from counterproof.models import ChangedFile, ChangeSet, Finding, Hunk
from counterproof.probes import pyast
from counterproof.probes.integrity.overfit import env_detection_findings, overfit_findings
from counterproof.probes.integrity.probe import run_integrity
from counterproof.probes.integrity.python_rules import (
    ChangedSource,
    TestFilePair,
    changed_test_ids,
    compare_test_files,
)

BASE_TESTS = dedent(
    """\
    import pytest
    from ledger.discounts import percent_discount


    def test_capped():
        assert percent_discount(200, 25, cap=30) == 30
        assert percent_discount(90, 50, cap=40) == 40


    def test_rounding():
        assert round_it(2.665) == 2.67


    def test_error():
        with pytest.raises(ValueError, match="percent"):
            percent_discount(10, 120)


    def test_close():
        assert total() == pytest.approx(10.0, rel=0.001)


    class TestCurrency:
        def test_mismatch(self):
            assert convert("USD") == "EUR"
    """
)


def _rules(base: str | None, head: str | None, **kwargs: object) -> list[tuple[str, str]]:
    pair = TestFilePair(
        path="tests/test_d.py",
        base_path=None if base is None else "tests/test_d.py",
        base_text=base,
        head_text=head,
    )
    findings = compare_test_files([pair], kwargs.get("sources", []), None)  # type: ignore[arg-type]
    return [(f.rule_id, f.severity) for f in findings]


def test_unchanged_tests_have_no_findings() -> None:
    assert _rules(BASE_TESTS, BASE_TESTS) == []


def test_fr_in_01_assertion_removed() -> None:
    head = BASE_TESTS.replace("    assert percent_discount(90, 50, cap=40) == 40\n", "")
    assert head != BASE_TESTS
    assert _rules(BASE_TESTS, head) == [("FR-IN-01", "high")]


def test_fr_in_01_pytest_raises_counts_as_assertion() -> None:
    head = BASE_TESTS.replace(
        '    with pytest.raises(ValueError, match="percent"):\n        percent_discount(10, 120)\n',
        "    percent_discount(10, 120)\n",
    )
    assert ("FR-IN-01", "high") in _rules(BASE_TESTS, head)


def test_fr_in_02_test_deleted() -> None:
    head = BASE_TESTS.replace("def test_rounding():\n    assert round_it(2.665) == 2.67\n", "")
    assert _rules(BASE_TESTS, head) == [("FR-IN-02", "high")]


def test_fr_in_02_renamed_with_same_body_is_not_flagged() -> None:
    head = BASE_TESTS.replace("def test_rounding():", "def test_rounding_half_up():")
    assert _rules(BASE_TESTS, head) == []


def test_fr_in_02_deleted_file() -> None:
    assert sorted(set(_rules(BASE_TESTS, None))) == [("FR-IN-02", "high")]


def test_fr_in_02_test_moved_to_another_file() -> None:
    moved = "def test_rounding():\n    assert round_it(2.665) == 2.67\n"
    head = BASE_TESTS.replace(moved, "")
    pairs = [
        TestFilePair("tests/test_d.py", "tests/test_d.py", BASE_TESTS, head),
        TestFilePair("tests/test_r.py", None, None, moved),
    ]
    assert compare_test_files(pairs, [], None) == []


@pytest.mark.parametrize(
    "marker",
    [
        '@pytest.mark.skip(reason="flaky")\n',
        "@pytest.mark.xfail\n",
        '@pytest.mark.skipif(True, reason="ci")\n',
    ],
)
def test_fr_in_03_skip_added(marker: str) -> None:
    head = BASE_TESTS.replace("def test_rounding():", marker + "def test_rounding():")
    assert _rules(BASE_TESTS, head) == [("FR-IN-03", "high")]


def test_fr_in_03_skip_call_and_module_mark() -> None:
    head = BASE_TESTS.replace(
        "    assert round_it(2.665) == 2.67\n",
        '    pytest.skip("later")\n    assert round_it(2.665) == 2.67\n',
    )
    assert _rules(BASE_TESTS, head) == [("FR-IN-03", "high")]
    module_mark = BASE_TESTS.replace(
        "from ledger.discounts", "pytestmark = pytest.mark.skip\nfrom ledger.discounts"
    )
    assert set(_rules(BASE_TESTS, module_mark)) == {("FR-IN-03", "high")}


def test_fr_in_03_new_skipped_test() -> None:
    head = (
        BASE_TESTS + '\n\n@pytest.mark.skip(reason="todo")\ndef test_new():\n    assert f() == 1\n'
    )
    assert _rules(BASE_TESTS, head) == [("FR-IN-03", "high")]


def test_fr_in_04_equality_turned_into_truthiness() -> None:
    head = BASE_TESTS.replace("assert round_it(2.665) == 2.67", "assert round_it(2.665)")
    assert _rules(BASE_TESTS, head) == [("FR-IN-04", "medium")]


def test_fr_in_04_equality_turned_into_in_check() -> None:
    head = BASE_TESTS.replace('assert convert("USD") == "EUR"', 'assert "E" in convert("USD")')
    assert _rules(BASE_TESTS, head) == [("FR-IN-04", "medium")]


def test_fr_in_04_approx_tolerance_increased() -> None:
    head = BASE_TESTS.replace("rel=0.001", "rel=0.5")
    assert _rules(BASE_TESTS, head) == [("FR-IN-04", "medium")]
    tighter = BASE_TESTS.replace("rel=0.001", "rel=0.0001")
    assert _rules(BASE_TESTS, tighter) == []


def test_fr_in_04_raises_match_removed() -> None:
    head = BASE_TESTS.replace(
        'pytest.raises(ValueError, match="percent")', "pytest.raises(ValueError)"
    )
    assert _rules(BASE_TESTS, head) == [("FR-IN-04", "medium")]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("assert round_it(2.665) == 2.67", "assert True"),
        ("assert round_it(2.665) == 2.67", "assert round_it(2.665) == 2.67 or True"),
        (
            "    assert round_it(2.665) == 2.67\n",
            "    try:\n        assert round_it(2.665) == 2.67\n    except AssertionError:\n        pass\n",
        ),
        (
            "    assert round_it(2.665) == 2.67\n",
            "    try:\n        assert round_it(2.665) == 2.67\n    except Exception:\n        pass\n",
        ),
    ],
)
def test_fr_in_05_assertion_neutralised(old: str, new: str) -> None:
    rules = _rules(BASE_TESTS, BASE_TESTS.replace(old, new))
    assert ("FR-IN-05", "high") in rules


def test_fr_in_05_try_that_reraises_is_fine() -> None:
    head = BASE_TESTS.replace(
        "    assert round_it(2.665) == 2.67\n",
        "    try:\n        assert round_it(2.665) == 2.67\n    except AssertionError:\n        log()\n        raise\n",
    )
    assert _rules(BASE_TESTS, head) == []


def test_fr_in_05_try_catching_specific_error_is_fine() -> None:
    head = BASE_TESTS.replace(
        "    assert round_it(2.665) == 2.67\n",
        "    try:\n        assert round_it(2.665) == 2.67\n    except KeyError:\n        pass\n",
    )
    assert _rules(BASE_TESTS, head) == []


def test_fr_in_05_new_neutralised_test() -> None:
    head = BASE_TESTS + "\n\ndef test_new():\n    assert f() == 1 or True\n"
    assert _rules(BASE_TESTS, head) == [("FR-IN-05", "high")]


def test_fr_in_06_new_mock_of_unit_under_test() -> None:
    sources = [
        ChangedSource(
            "src/ledger/discounts.py", "ledger.discounts", frozenset({"percent_discount"})
        )
    ]
    head = BASE_TESTS.replace(
        "def test_rounding():\n",
        'def test_rounding(mocker):\n    mocker.patch("ledger.discounts.percent_discount")\n',
    )
    assert ("FR-IN-06", "medium") in _rules(BASE_TESTS, head, sources=sources)
    unrelated = BASE_TESTS.replace(
        "def test_rounding():\n",
        'def test_rounding(mocker):\n    mocker.patch("ledger.network.fetch")\n',
    )
    assert ("FR-IN-06", "medium") not in _rules(BASE_TESTS, unrelated, sources=sources)


def test_fr_in_06_patch_object_and_monkeypatch() -> None:
    sources = [
        ChangedSource(
            "src/ledger/discounts.py", "ledger.discounts", frozenset({"percent_discount"})
        )
    ]
    head = BASE_TESTS.replace(
        "def test_rounding():\n",
        'def test_rounding(monkeypatch):\n    monkeypatch.setattr(discounts, "percent_discount", fake)\n',
    )
    assert ("FR-IN-06", "medium") in _rules(BASE_TESTS, head, sources=sources)


def test_changed_test_ids_for_red_green() -> None:
    head = BASE_TESTS.replace("2.67", "2.68") + "\n\ndef test_new():\n    assert f() == 1\n"
    pair = TestFilePair("tests/test_d.py", "tests/test_d.py", BASE_TESTS, head)
    assert changed_test_ids([pair]) == [
        "tests/test_d.py::test_new",
        "tests/test_d.py::test_rounding",
    ]


def test_unparseable_files_are_skipped_not_crashed() -> None:
    assert _rules("def broken(:\n", "def also broken(:\n") == []
    assert pyast.analyse_test_file("def (") is None


# -- FR-IN-07 / FR-IN-08 --------------------------------------------------------------------

TEST_LITERALS = frozenset(
    pyast.analyse_test_file(
        dedent(
            """\
            def test_x():
                assert f(7) == 13
                assert g(Money.of("200.00"), 25) == Money.of("30.00")
                assert h("USD") == 1
                assert k([1, 1, 1]) == [Money.of("33.34")]
            """
        )
    ).literals  # type: ignore[union-attr]
)


def _overfit(source: str, lines: set[int] | None = None) -> list[tuple[str, int | None]]:
    added = lines if lines is not None else set(range(1, len(source.splitlines()) + 1))
    return [
        (f.severity, f.line) for f in overfit_findings("src/a.py", source, added, TEST_LITERALS)
    ]


def test_fr_in_07_equality_to_test_input_returning_expected_value_is_high() -> None:
    assert _overfit("def f(n):\n    if n == 7:\n        return 13\n    return n * 2\n") == [
        ("high", 2)
    ]


def test_fr_in_07_constructor_literals_and_boolean_chains() -> None:
    source = (
        "def g(total, rate):\n"
        '    if total == Money.of("200.00") and rate == 25:\n'
        '        return Money.of("30.00")\n'
        "    return total\n"
    )
    assert _overfit(source) == [("high", 2), ("high", 2)]


def test_fr_in_07_sequence_literal() -> None:
    source = 'def k(r):\n    if r == [1, 1, 1]:\n        return [Money.of("33.34")]\n    return r\n'
    assert _overfit(source) == [("high", 2)]


def test_fr_in_07_lookup_table_keyed_by_test_inputs() -> None:
    assert _overfit("def f(n):\n    return {7: 13, 8: 15}[n]\n") == [("high", 2)]
    assert _overfit("def f(n):\n    return {7: 13}.get(n, 0)\n") == [("high", 2)]


def test_fr_in_07_match_statement() -> None:
    source = "def f(n):\n    match n:\n        case 7:\n            return 13\n    return 0\n"
    assert _overfit(source) == [("high", 3)]


def test_fr_in_07_comparison_without_test_output_is_medium() -> None:
    assert _overfit('def h(c):\n    if c == "USD":\n        return rate()\n    return 2\n') == [
        ("medium", 2)
    ]


@pytest.mark.parametrize(
    "source",
    [
        "def f(n):\n    if n > 7:\n        return 13\n    return 0\n",  # ordering: a threshold
        "def f(n):\n    if n == 0:\n        return 1\n    return n\n",  # trivial literals
        "def f(n):\n    if n == 99:\n        return 13\n    return n\n",  # 99 not in tests
        "def f(n):\n    return n * 7\n",  # literal not compared
    ],
)
def test_fr_in_07_negatives(source: str) -> None:
    assert _overfit(source) == []


def test_fr_in_07_only_added_lines_count() -> None:
    source = "def f(n):\n    if n == 7:\n        return 13\n    return n * 2\n"
    assert _overfit(source, lines={4}) == []


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ('    if os.environ.get("PYTEST_CURRENT_TEST"):', True),
        ('    if "pytest" in sys.modules:', True),
        ('    if sys.modules.get("pytest"):', True),
        ('    if os.getenv("COUNTERPROOF_RUN"):', True),
        ('    debug = os.environ.get("DEBUG")', False),
        ("    import pytest_helpers_for_docs", False),
    ],
)
def test_fr_in_08_test_environment_detection(line: str, flagged: bool) -> None:
    assert bool(env_detection_findings("src/a.py", [(3, line)])) is flagged


# -- probe: FR-IN-09, snapshots, waivers ----------------------------------------------------


def _change(
    files: list[ChangedFile], hunks: list[Hunk], flags: list[str] | None = None
) -> ChangeSet:
    return ChangeSet(
        repo_root=Path("/r"),
        base_sha="a" * 40,
        head_ref="WORKTREE",
        head_snapshot_sha="b" * 40,
        files=files,
        hunks=hunks,
        intent="bugfix",
        intent_source="rule",
        flags=flags or [],
    )


HARDCODED_BASE = "def f(n):\n    return n * 2\n"
HARDCODED_HEAD = "def f(n):\n    if n == 7:\n        return 13\n    return n * 2\n"
TESTS = "def test_f():\n    assert f(7) == 13\n"


def _probe(head_source: str, *, base_source: str = HARDCODED_BASE, flags: list[str] | None = None):  # type: ignore[no-untyped-def]
    files = {
        "src/a.py": (base_source, head_source),
        "tests/test_a.py": (TESTS, TESTS),
    }
    change = _change(
        [
            ChangedFile(path="src/a.py", role="source", status="modified"),
            ChangedFile(path="tests/test_a.py", role="test", status="modified"),
        ],
        [
            Hunk(
                path="src/a.py",
                old_start=1,
                old_len=0,
                new_start=2,
                new_len=head_source.count("\n") - 2,
            )
        ],
        flags,
    )
    return run_integrity(
        change,
        read_base=lambda p: files[p][0],
        read_head=lambda p: files[p][1],
        literal_test_files=["tests/test_a.py"],
        covering_by_file=None,
    )


def test_probe_reports_high_overfit() -> None:
    result = _probe(HARDCODED_HEAD)
    assert result.signals == ["IN_HIGH"]
    assert [(f.rule_id, f.waived) for f in result.findings] == [("FR-IN-07", False)]
    assert result.metrics["findings_high"] == 1.0


def test_waiver_added_by_the_change_is_not_applied() -> None:
    head = HARDCODED_HEAD.replace(
        "    if n == 7:", "    # counterproof: allow FR-IN-07 legacy customer id\n    if n == 7:"
    )
    result = _probe(head)
    (only,) = result.findings
    assert not only.waived
    assert any("proposed waiver" in ref for ref in only.evidence_refs)
    assert result.signals == ["IN_HIGH"]


def test_waiver_already_in_base_is_applied() -> None:
    base = "def f(n):\n    # counterproof: allow FR-IN-07 legacy customer id\n    return n * 2\n"
    head = "def f(n):\n    # counterproof: allow FR-IN-07 legacy customer id\n    if n == 7:\n        return 13\n    return n * 2\n"
    files = {"src/a.py": (base, head), "tests/test_a.py": (TESTS, TESTS)}
    change = _change(
        [ChangedFile(path="src/a.py", role="source", status="modified")],
        [Hunk(path="src/a.py", old_start=2, old_len=0, new_start=3, new_len=2)],
    )
    result = run_integrity(
        change,
        read_base=lambda p: files[p][0],
        read_head=lambda p: files[p][1],
        literal_test_files=["tests/test_a.py"],
        covering_by_file=None,
    )
    (only,) = result.findings
    assert only.waived and only.waiver_reason == "legacy customer id"
    assert result.signals == ["IN_CLEAN"]


def test_fr_in_09_self_config_change_is_medium() -> None:
    result = _probe(HARDCODED_BASE + "\n", flags=["self_config_change"])
    assert [(f.rule_id, f.severity) for f in result.findings] == [("FR-IN-09", "medium")]
    assert result.signals == ["IN_MEDIUM"]


def test_clean_change() -> None:
    result = _probe("def f(n):\n    if n > 0:\n        return n * 2\n    return 0\n")
    assert result.findings == [] and result.signals == ["IN_CLEAN"]


def test_literal_canonicalisation() -> None:
    import libcst as cst  # noqa: PLC0415

    def lit(code: str) -> pyast.Literal | None:
        return pyast.literal_of(cst.parse_expression(code))

    assert lit('Decimal("2.50")') == lit("2.5") == ("num", "2.5")
    assert lit('Money.of("20.00")') == lit("20") == ("num", "20")
    assert lit("-3") == ("num", "-3")
    assert lit("'USD'") == ("str", "USD")
    assert lit("[1, 2]") == ("seq", "(num:1,num:2)")
    assert lit("f(x)") is None
    assert pyast.is_trivial(("num", "0")) and not pyast.is_trivial(("num", "7"))


def test_added_symbols_and_module_names() -> None:
    base = "def f(a):\n    pass\n"
    head = "def f(a, cap=None):\n    pass\n\nclass Ledger:\n    def split(self):\n        pass\n"
    assert pyast.added_symbols(base, head) == {"f(cap)", "Ledger", "split", "split(self)"}
    assert pyast.module_name("src/ledger/discounts.py") == "ledger.discounts"
    assert pyast.module_name("pkg/__init__.py") == "pkg"


def test_findings_round_trip_as_models() -> None:
    result = _probe(HARDCODED_HEAD)
    assert all(isinstance(f, Finding) for f in result.findings)
    assert result.findings[0].head_snippet == "if n == 7:"
