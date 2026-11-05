"""Section 8 verdict rules 1-5 (``early_verdict``): table-driven, including rule order.

Rules 6-14 and the full order are covered in test_verdict_engine.py."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from counterproof.config import build_config
from counterproof.models import BaselineReport, Finding, ProbeResult, Severity
from counterproof.verdict.rules import RULES, DecisionInput, decide, early_verdict

CONFIG = build_config()


def _baseline(*, flaky: int = 0, failing: int = 0, total: int = 10) -> BaselineReport:
    tests = [f"t::{i}" for i in range(total)]
    status = "ok"
    if flaky / total > 0.2:
        status = "flaky_suite"
    elif failing:
        status = "head_red"
    return BaselineReport(
        runs=3,
        status=status,  # type: ignore[arg-type]
        selected=tests,
        flaky=tests[:flaky],
        failing=tests[flaky : flaky + failing],
        flaky_ratio=flaky / total,
    )


def _integrity(*findings: tuple[str, Severity, bool]) -> ProbeResult:
    return ProbeResult(
        probe="integrity",
        status="ok",
        findings=[
            Finding(rule_id=r, severity=s, message="m", file="f.py", line=1, waived=w)
            for r, s, w in findings
        ],
    )


def _claim(*signals: str) -> ProbeResult:
    return ProbeResult(probe="claim_audit", status="ok", signals=list(signals))


@dataclass
class Case:
    name: str
    build_error: str | None = None
    baseline: BaselineReport | None = field(default_factory=_baseline)
    probes: tuple[ProbeResult, ...] = ()
    verdict: str = "INCONCLUSIVE"
    rule_id: str | None = None  # None: no rule 1-5 matches
    reason_rules: tuple[str, ...] = ()


CASES = [
    Case("all clear", reason_rules=()),
    Case(
        "1 build error",
        build_error="cannot collect",
        rule_id="R-INC-BUILD",
        reason_rules=("R-INC-BUILD",),
    ),
    Case(
        "1 no selected tests",
        baseline=_baseline(total=1).model_copy(update={"selected": [], "status": "no_tests"}),
        rule_id="R-INC-BUILD",
        reason_rules=("R-INC-BUILD",),
    ),
    Case(
        "2 flaky suite",
        baseline=_baseline(flaky=3),
        rule_id="R-INC-FLAKY",
        reason_rules=("R-INC-FLAKY",),
    ),
    Case("2 at threshold is not flaky", baseline=_baseline(flaky=2)),
    Case(
        "3 false green",
        probes=(_claim("CLAIM_FALSE_GREEN"),),
        baseline=_baseline(failing=1),
        verdict="REFUTED",
        rule_id="R-REF-CLAIM",
        reason_rules=("R-REF-CLAIM", "R-INC-HEADRED"),
    ),
    Case(
        "3 no run with failing baseline",
        probes=(_claim("CLAIM_NO_RUN"),),
        baseline=_baseline(failing=2),
        verdict="REFUTED",
        rule_id="R-REF-CLAIM",
        reason_rules=("R-REF-CLAIM", "R-INC-HEADRED"),
    ),
    Case("3 no run with green baseline is not rule 3", probes=(_claim("CLAIM_NO_RUN"),)),
    Case(
        "4 head red",
        baseline=_baseline(failing=1),
        rule_id="R-INC-HEADRED",
        reason_rules=("R-INC-HEADRED",),
    ),
    Case(
        "5 high integrity finding",
        probes=(_integrity(("FR-IN-07", "high", False)),),
        verdict="REFUTED",
        rule_id="R-REF-INTEGRITY",
        reason_rules=("R-REF-INTEGRITY",),
    ),
    Case("5 waived finding does not refute", probes=(_integrity(("FR-IN-07", "high", True)),)),
    Case("5 medium finding does not refute", probes=(_integrity(("FR-IN-04", "medium", False)),)),
    Case("5 FR-IN-07 medium does not refute", probes=(_integrity(("FR-IN-07", "medium", False)),)),
    Case(
        "order: build beats everything",
        build_error="x",
        baseline=_baseline(flaky=5, failing=2),
        probes=(_integrity(("FR-IN-01", "high", False)),),
        rule_id="R-INC-BUILD",
        reason_rules=("R-INC-BUILD", "R-INC-FLAKY", "R-INC-HEADRED", "R-REF-INTEGRITY"),
    ),
    Case(
        "order: flaky beats claim and head red",
        baseline=_baseline(flaky=4, failing=1),
        probes=(_claim("CLAIM_FALSE_GREEN"),),
        rule_id="R-INC-FLAKY",
        reason_rules=("R-INC-FLAKY", "R-REF-CLAIM", "R-INC-HEADRED"),
    ),
    Case(
        "order: head red beats integrity",
        baseline=_baseline(failing=1),
        probes=(_integrity(("FR-IN-02", "high", False)),),
        rule_id="R-INC-HEADRED",
        reason_rules=("R-INC-HEADRED", "R-REF-INTEGRITY"),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_section_8_rules_1_to_5(case: Case) -> None:
    inp = DecisionInput(
        intent="bugfix", build_error=case.build_error, baseline=case.baseline, probes=case.probes
    )
    verdict = early_verdict(inp, CONFIG)
    if case.rule_id is None:
        assert verdict is None
        return
    assert verdict is not None
    assert (verdict.verdict, verdict.rule_id) == (case.verdict, case.rule_id)
    matched = tuple(
        r
        for r in [rule.rule_id for rule in RULES]
        if any(x.startswith(f"[{r}]") for x in verdict.reasons)
    )
    assert matched == case.reason_rules


def test_rules_are_listed_in_prd_order() -> None:
    assert [(r.number, r.rule_id) for r in RULES[:5]] == [
        (1, "R-INC-BUILD"),
        (2, "R-INC-FLAKY"),
        (3, "R-REF-CLAIM"),
        (4, "R-INC-HEADRED"),
        (5, "R-REF-INTEGRITY"),
    ]


def test_decide_is_pure_and_deterministic() -> None:
    inp = DecisionInput(
        intent="bugfix", baseline=_baseline(), probes=(_integrity(("FR-IN-03", "high", False)),)
    )
    assert decide(inp, CONFIG) == decide(inp, CONFIG)


def test_flaky_threshold_comes_from_config() -> None:
    strict = build_config({"flaky": {"max_ratio": 0.05}})
    inp = DecisionInput(intent="bugfix", baseline=_baseline(flaky=1))
    assert early_verdict(inp, CONFIG) is None
    assert decide(inp, strict).rule_id == "R-INC-FLAKY"
