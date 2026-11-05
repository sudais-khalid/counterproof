"""Section 8 rules 6-14 (table-driven), the R-PROVEN conditions, rule order across all 14
rules, and the --fail-on exit policy."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from counterproof.config import build_config
from counterproof.models import BaselineReport, Finding, Intent, ProbeResult, Severity, Verdict
from counterproof.verdict.engine import exit_code_for
from counterproof.verdict.rules import RULES, DecisionInput, decide, early_verdict

CONFIG = build_config()
GREEN = BaselineReport(runs=3, status="ok", selected=[f"t::{i}" for i in range(10)])


def rg(*signals: str) -> ProbeResult:
    return ProbeResult(probe="red_green", status="ok", signals=list(signals))


def mu(killed: int, valid: int, *signals: str, high: int = 0) -> ProbeResult:
    metrics = {
        "killed": float(killed),
        "valid": float(valid),
        "high_plausibility_survivors": float(high),
    }
    if valid:
        metrics["kill_ratio"] = killed / valid
    return ProbeResult(probe="mutants", status="ok", signals=list(signals), metrics=metrics)


def integrity(*findings: tuple[str, Severity]) -> ProbeResult:
    return ProbeResult(
        probe="integrity",
        status="ok",
        findings=[
            Finding(rule_id=r, severity=s, message="m", file="f.py", line=1) for r, s in findings
        ],
    )


def oracle_bug() -> ProbeResult:
    return ProbeResult(
        probe="blind_oracle",
        status="ok",
        findings=[Finding(rule_id="BO-LIKELY-BUG", severity="medium", message="total at cap")],
    )


def claim(*signals: str) -> ProbeResult:
    return ProbeResult(probe="claim_audit", status="ok", signals=list(signals))


@dataclass
class Case:
    name: str
    probes: tuple[ProbeResult, ...]
    verdict: str
    rule_id: str
    intent: Intent = "bugfix"
    matched: tuple[str, ...] = field(default=())


CASES = [
    Case(
        "14 proven: strong red-green, 8/8",
        (integrity(), rg("RG_STRONG"), mu(8, 8)),
        "PROVEN",
        "R-PROVEN",
    ),
    Case("14 proven at exactly 0.8", (rg("RG_STRONG"), mu(8, 10)), "PROVEN", "R-PROVEN"),
    Case(
        "14 proven: api-only with every mutant killed",
        (rg("RG_API_ONLY"), mu(5, 5)),
        "PROVEN",
        "R-PROVEN",
    ),
    Case(
        "14 proven: refactor with pass-both",
        (rg("RG_PASS_BOTH"), mu(4, 5)),
        "PROVEN",
        "R-PROVEN",
        intent="refactor",
    ),
    Case("14 proven: test-only", (mu(3, 3),), "PROVEN", "R-PROVEN", intent="test_only"),
    Case(
        "14 fallback: bugfix with no red-green",
        (rg("RG_PASS_BOTH"), mu(9, 10)),
        "WEAK",
        "R-WEAK-NOREDGREEN",
    ),
    Case(
        "14 fallback: red-green inconclusive",
        (rg("RG_UNCLEAR"), mu(5, 5)),
        "WEAK",
        "R-WEAK-NOREDGREEN",
    ),
    Case(
        "6 overfit survived",
        (rg("RG_STRONG"), mu(9, 10, "MU_OVERFIT_SURVIVED")),
        "UNPROVEN",
        "R-UNP-OVERFIT",
    ),
    Case(
        "7 no red-green and weak mutants",
        (rg("RG_PASS_BOTH"), mu(7, 10)),
        "UNPROVEN",
        "R-UNP-NOREDGREEN",
        matched=("R-UNP-NOREDGREEN", "R-WEAK-SURVIVOR"),
    ),
    Case(
        "7 no candidates and no mutants",
        (rg("RG_NO_CANDIDATES"), mu(0, 0)),
        "UNPROVEN",
        "R-UNP-NOREDGREEN",
        matched=("R-UNP-NOREDGREEN", "R-WEAK-FEWMUTANTS"),
    ),
    Case(
        "7 feature too",
        (rg("RG_PASS_BOTH"), mu(1, 4)),
        "UNPROVEN",
        "R-UNP-NOREDGREEN",
        intent="feature",
        matched=("R-UNP-NOREDGREEN", "R-UNP-WEAKTESTS"),
    ),
    Case(
        "7 not for refactor",
        (rg("RG_PASS_BOTH"), mu(7, 10)),
        "WEAK",
        "R-WEAK-SURVIVOR",
        intent="refactor",
    ),
    Case("8 weak tests", (rg("RG_STRONG"), mu(1, 4)), "UNPROVEN", "R-UNP-WEAKTESTS"),
    Case("8 needs three mutants", (rg("RG_STRONG"), mu(0, 2)), "WEAK", "R-WEAK-FEWMUTANTS"),
    Case(
        "8 test-only that kills nothing",
        (mu(0, 1),),
        "UNPROVEN",
        "R-UNP-WEAKTESTS",
        intent="test_only",
        matched=("R-UNP-WEAKTESTS", "R-WEAK-FEWMUTANTS"),
    ),
    Case("9 kill ratio in [0.5, 0.8)", (rg("RG_STRONG"), mu(3, 4)), "WEAK", "R-WEAK-SURVIVOR"),
    Case(
        "9 boundary 0.5 is weak not unproven",
        (rg("RG_STRONG"), mu(2, 4)),
        "WEAK",
        "R-WEAK-SURVIVOR",
    ),
    Case(
        "9 high-plausibility survivor",
        (rg("RG_STRONG"), mu(9, 10, high=1)),
        "WEAK",
        "R-WEAK-SURVIVOR",
    ),
    Case("10 api-only below 1.0", (rg("RG_API_ONLY"), mu(9, 10)), "WEAK", "R-WEAK-APIONLY"),
    Case(
        "11 oracle likely bug", (rg("RG_STRONG"), mu(8, 8), oracle_bug()), "WEAK", "R-WEAK-ORACLE"
    ),
    Case(
        "12 medium integrity finding",
        (integrity(("FR-IN-04", "medium")), rg("RG_STRONG"), mu(8, 8)),
        "WEAK",
        "R-WEAK-MEDIUM",
    ),
    Case(
        "12 weak-level claim",
        (rg("RG_STRONG"), mu(8, 8), claim("CLAIM_SCOPE")),
        "WEAK",
        "R-WEAK-MEDIUM",
    ),
    Case("13 few mutants", (rg("RG_STRONG"), mu(2, 2)), "WEAK", "R-WEAK-FEWMUTANTS"),
    Case(
        "order: 5 integrity beats 6 overfit",
        (integrity(("FR-IN-07", "high")), rg("RG_STRONG"), mu(1, 4, "MU_OVERFIT_SURVIVED")),
        "REFUTED",
        "R-REF-INTEGRITY",
        matched=("R-REF-INTEGRITY", "R-UNP-OVERFIT", "R-UNP-WEAKTESTS"),
    ),
    Case(
        "order: 6 overfit beats 8 weak tests",
        (rg("RG_STRONG"), mu(1, 4, "MU_OVERFIT_SURVIVED")),
        "UNPROVEN",
        "R-UNP-OVERFIT",
        matched=("R-UNP-OVERFIT", "R-UNP-WEAKTESTS"),
    ),
    Case(
        "order: 9 survivor beats 12 medium",
        (integrity(("FR-IN-06", "medium")), rg("RG_STRONG"), mu(6, 8)),
        "WEAK",
        "R-WEAK-SURVIVOR",
        matched=("R-WEAK-SURVIVOR", "R-WEAK-MEDIUM"),
    ),
    Case(
        "order: 10 api-only beats 13 few mutants",
        (rg("RG_API_ONLY"), mu(1, 2)),
        "WEAK",
        "R-WEAK-SURVIVOR",
        matched=("R-WEAK-SURVIVOR", "R-WEAK-APIONLY", "R-WEAK-FEWMUTANTS"),
    ),
]


def _matched(verdict: Verdict) -> tuple[str, ...]:
    ids = [r.rule_id for r in RULES] + ["R-PROVEN", "R-WEAK-NOREDGREEN"]
    return tuple(i for i in ids if any(reason.startswith(f"[{i}]") for reason in verdict.reasons))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_section_8_rules_6_to_14(case: Case) -> None:
    inp = DecisionInput(intent=case.intent, baseline=GREEN, probes=case.probes)
    verdict = decide(inp, CONFIG)
    assert (verdict.verdict, verdict.rule_id) == (case.verdict, case.rule_id)
    assert _matched(verdict) == (case.matched or (case.rule_id,))


def test_all_fourteen_rules_in_prd_order() -> None:
    assert [(r.number, r.rule_id) for r in RULES] == [
        (1, "R-INC-BUILD"),
        (2, "R-INC-FLAKY"),
        (3, "R-REF-CLAIM"),
        (4, "R-INC-HEADRED"),
        (5, "R-REF-INTEGRITY"),
        (6, "R-UNP-OVERFIT"),
        (7, "R-UNP-NOREDGREEN"),
        (8, "R-UNP-WEAKTESTS"),
        (9, "R-WEAK-SURVIVOR"),
        (10, "R-WEAK-APIONLY"),
        (11, "R-WEAK-ORACLE"),
        (12, "R-WEAK-MEDIUM"),
        (13, "R-WEAK-FEWMUTANTS"),
    ]


def test_thresholds_come_from_config() -> None:
    lenient = build_config({"mutants": {"proven_at_least": 0.7, "unproven_below": 0.3}})
    inp = DecisionInput(intent="bugfix", baseline=GREEN, probes=(rg("RG_STRONG"), mu(3, 4)))
    assert decide(inp, CONFIG).verdict == "WEAK"
    assert decide(inp, lenient).verdict == "PROVEN"


def test_early_verdict_only_uses_rules_1_to_5() -> None:
    assert early_verdict(DecisionInput(intent="bugfix", baseline=GREEN), CONFIG) is None
    refuted = DecisionInput(
        intent="bugfix", baseline=GREEN, probes=(integrity(("FR-IN-03", "high")),)
    )
    verdict = early_verdict(refuted, CONFIG)
    assert verdict is not None and verdict.rule_id == "R-REF-INTEGRITY"


def test_confidence_note_states_what_was_not_checked() -> None:
    proven = decide(
        DecisionInput(intent="bugfix", baseline=GREEN, probes=(rg("RG_STRONG"), mu(8, 8))), CONFIG
    )
    assert "Tested against 8 plausible fault(s)" in proven.confidence_note
    assert "LLM disabled" in proven.confidence_note
    assert "not a proof of full program correctness" in proven.confidence_note


@pytest.mark.parametrize(
    ("verdict", "fail_on", "code"),
    [
        ("PROVEN", None, 0),
        ("WEAK", None, 10),
        ("REFUTED", None, 30),
        ("INCONCLUSIVE", None, 40),
        ("WEAK", "weak", 10),
        ("WEAK", "unproven", 0),
        ("UNPROVEN", "unproven", 20),
        ("UNPROVEN", "refuted", 0),
        ("REFUTED", "refuted", 30),
        ("PROVEN", "weak", 0),
        ("INCONCLUSIVE", "weak", 0),
    ],
)
def test_fail_on_exit_policy(verdict: str, fail_on: str | None, code: int) -> None:
    assert exit_code_for(Verdict.make(verdict, "R-X"), fail_on) == code  # type: ignore[arg-type]
