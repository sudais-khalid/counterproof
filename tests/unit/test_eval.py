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
