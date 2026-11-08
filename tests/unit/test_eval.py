"""M8 evaluation: corpus files, metrics, the published table (PRD section 17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from counterproof.errors import UsageError
from counterproof.eval.corpus import load_corpus
from counterproof.eval.harness import ABLATIONS, CaseResult, EvalReport, summarise
from counterproof.eval.table import render_table
from tests.helpers import TESTS_DIR

CORPORA = TESTS_DIR.parent / "eval" / "corpora"


def test_shipped_corpora_load() -> None:
    c1 = load_corpus(CORPORA / "c1")
    assert len(c1.cases) == 40 and [c.name for c in c1.configurations] == ["rules", "llm"]
    kinds = {c.kind for c in c1.cases}
    assert kinds == {"honest", "hard-coded", "weakened", "partial"}
    assert sum(c.label == "honest" for c in c1.cases) == 10
    assert c1.repos["ts_cart"].node_modules is not None
    c2 = load_corpus(CORPORA / "c2")
    assert len(c2.cases) == 8 and all(c.transcript and c.transcript.is_file() for c in c2.cases)
    with pytest.raises(UsageError, match=r"corpus.toml"):
        load_corpus(CORPORA / "c3")  # template only: C3 is not run yet


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('name = "x"\n', "no cases"),
        ('[[cases]]\nid = "a"\nlabel = "sneaky"\n', "label must be 'honest' or 'gamed'"),
        ('[[cases]]\nid = "a"\nlabel = "honest"\ngit = "https://x/y"\n', "needs base and head"),
        ('[[cases]]\nid = "a"\nlabel = "honest"\nrepo = "nope"\ntask = "T"\n', "known repo"),
        ("not = [toml", "corpus.toml"),
    ],
)
def test_corpus_errors_are_usage_errors(tmp_path: Path, toml: str, message: str) -> None:
    (tmp_path / "corpus.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(UsageError, match=message):
        load_corpus(tmp_path)


def _result(
    case: str,
    label: str,
    kind: str,
    verdict: str,
    *,
    ablations: dict[str, str] | None = None,
    baselines: dict[str, bool | None] | None = None,
    duration: float = 10.0,
) -> CaseResult:
    return CaseResult(
        case=case,
        config="rules",
        label=label,
        kind=kind,
        verdict=verdict,
        rule_id="R",
        flagged_strict=verdict in {"UNPROVEN", "REFUTED"},
        flagged_lenient=verdict in {"WEAK", "UNPROVEN", "REFUTED"},
        baselines=baselines or {"B0": False, "B1": None, "B2": True},
        ablations={**dict.fromkeys(ABLATIONS, verdict), **(ablations or {})},
        duration_s=duration,
        recorded_usd=0.0,
        run_id="r",
        engine_consistent=True,
    )


def test_metrics_follow_prd_definitions() -> None:
    results = [
        _result("a", "gamed", "hard-coded", "REFUTED", ablations={"integrity": "PROVEN"}),
        _result("b", "gamed", "partial", "WEAK"),
        _result(
            "c", "gamed", "partial", "PROVEN", baselines={"B0": False, "B1": True, "B2": False}
        ),
        _result("d", "honest", "honest", "PROVEN", duration=30.0),
        _result("e", "honest", "honest", "UNPROVEN"),
    ]
    s = summarise(results)["rules"]
    assert (s["gamed"], s["honest"]) == (3, 2)
    assert s["recall_strict"] == 0.333 and s["recall_lenient"] == 0.667
    assert s["false_alarm_strict"] == 0.5 and s["false_alarm_lenient"] == 0.5
    assert s["by_kind"]["partial"] == {"cases": 2, "flagged_strict": 0, "flagged_lenient": 1}
    assert s["baselines"]["B1"] == {"applicable": 1, "recall": 0.333, "false_alarm": 0.0}
    assert s["baselines"]["B2"]["recall"] == 0.667 and s["baselines"]["B2"]["false_alarm"] == 1.0
    assert s["ablations"]["integrity"]["recall_strict"] == 0.0  # only P2 caught "a"
    assert s["median_runtime_s"] == 10.0


def test_table_is_reproducible_and_complete() -> None:
    results = [_result("x", "gamed", "partial", "WEAK"), _result("y", "honest", "honest", "PROVEN")]
    report = EvalReport("C", "desc", "2026-11-08", "0.1", "00", results, summarise(results))
    table = render_table(report)
    assert table == render_table(report)
    assert "| Counterproof (rules) | 0% | 100% | 0% | 0% | 1 gamed, 1 honest |" in table
    assert "| B1 claim audit only (applies to 0 of 2 cases) |" in table
    assert "| rule mutants only (no LLM mutants) |" in table
    assert "| x | gamed | partial | WEAK (R) |" in table
    report.baselines = ["B0"]
    assert "B1 claim audit only" not in render_table(report)


def test_determinism_is_verdict_level_with_evidence_reported_separately() -> None:
    same = _result("a", "honest", "honest", "PROVEN")
    same.repeat_verdicts, same.repeats_identical = ["PROVEN (R)"], True
    noisy = _result("b", "gamed", "partial", "WEAK")
    noisy.repeat_verdicts, noisy.repeats_identical = ["WEAK (R)"], False  # e.g. timeout vs fail
    flipped = _result("c", "gamed", "partial", "WEAK")
    flipped.repeat_verdicts, flipped.repeats_identical = ["PROVEN (R)"], False
    s = summarise([same, noisy, flipped])["rules"]
    assert s["determinism"] == 0.667 and s["evidence_determinism"] == 0.333


def test_base_overlay_is_committed_before_the_change_and_deletions_apply(tmp_path: Path) -> None:
    """Real agent sessions (C2): files the agent was given (a test it must not edit) belong to
    the base commit, and files the agent deleted are absent from the head commit."""
    from counterproof.eval.corpus import materialise  # noqa: PLC0415
    from tests.helpers import git  # noqa: PLC0415

    fixtures = tmp_path / "fx"
    (fixtures / "base" / "src").mkdir(parents=True)
    (fixtures / "base" / "src" / "m.py").write_text("X = 1\n", encoding="utf-8")
    (fixtures / "tasks" / "T1").mkdir(parents=True)
    (fixtures / "tasks" / "T1" / "task.md").write_text("Fix X.\n", encoding="utf-8")
    given = tmp_path / "given" / "tests"
    given.mkdir(parents=True)
    (given / "test_given.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    change = tmp_path / "change" / "src"
    change.mkdir(parents=True)
    (change / "m.py").write_text("X = 2\n", encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "corpus.toml").write_text(
        f'[[repos]]\nname = "r"\nfixtures = "{fixtures.as_posix()}"\nall_variants = false\n'
        f'[[cases]]\nid = "c"\nrepo = "r"\ntask = "T1"\nlabel = "gamed"\n'
        f'overlay = "{(tmp_path / "change").as_posix()}"\n'
        f'base_overlay = "{(tmp_path / "given").as_posix()}"\n'
        'deletions = ["tests/test_given.py"]\n',
        encoding="utf-8",
    )
    corpus = load_corpus(corpus_dir)
    (case,) = corpus.cases
    built = materialise(corpus, case, tmp_path / "repo")
    base_files = git(built.path, "ls-tree", "-r", "--name-only", built.base).split()
    head_files = git(built.path, "ls-tree", "-r", "--name-only", built.head).split()
    assert "tests/test_given.py" in base_files and "tests/test_given.py" not in head_files
    assert git(built.path, "show", f"{built.head}:src/m.py") == "X = 2\n"
    assert built.task_text == "Fix X."


def test_partial_reports_merge_into_one_with_a_recomputed_summary(tmp_path: Path) -> None:
    """C3 runs one pull request per CI job; the per-job reports merge into one table."""
    from counterproof.eval.harness import (  # noqa: PLC0415
        load_report,
        merge_reports,
        write_report,
    )

    def report(*results: CaseResult) -> EvalReport:
        return EvalReport(
            corpus="part",
            description="",
            created_at="2026-11-08T00:00:00+00:00",
            tool_version="1.0.0",
            seed="00" * 16,
            results=list(results),
            summary=summarise(list(results)),
        )

    one = write_report(report(_result("b", "honest", "honest", "WEAK")), tmp_path / "one")[0]
    two = write_report(report(_result("a", "honest", "honest", "PROVEN")), tmp_path / "two")[0]
    merged = merge_reports(
        [load_report(one), load_report(two)], corpus="C3", description="real pull requests"
    )
    assert [r.case for r in merged.results] == ["a", "b"]
    assert merged.summary["rules"]["honest"] == 2
    assert merged.summary["rules"]["false_alarm_strict"] == 0.0
    assert merged.summary["rules"]["false_alarm_lenient"] == 0.5
    assert merged.corpus == "C3" and merged.tool_version == "1.0.0"
    with pytest.raises(ValueError, match="no reports"):
        merge_reports([], corpus="C3", description="")
