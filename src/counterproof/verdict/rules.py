"""Verdict rules (PRD section 8): a pure function from typed results to a :class:`Verdict`.

Rules are evaluated top to bottom. The first matching rule sets the verdict and rule id;
every matching rule contributes reasons, in rule order. No I/O, no clock, no randomness.

Implemented so far: rules 1 to 5. Rules 6 to 14 need mutant results (P3) and land in M3;
until then a change that passes rules 1 to 5 is ``INCONCLUSIVE / NOT_IMPLEMENTED`` with the
available probe evidence listed as reasons.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from counterproof.config import Config
from counterproof.models import (
    BaselineReport,
    Intent,
    ProbeName,
    ProbeResult,
    Verdict,
    VerdictName,
)

NOT_IMPLEMENTED: Final = "NOT_IMPLEMENTED"
# FR-IN rules whose high-severity findings refute a change (rule 5).
REFUTING_INTEGRITY_RULES: Final = frozenset(
    {"FR-IN-01", "FR-IN-02", "FR-IN-03", "FR-IN-05", "FR-IN-07", "FR-IN-08"}
)


@dataclass(frozen=True)
class DecisionInput:
    intent: Intent
    build_error: str | None = None
    """Rule 1: prepare/collect failure, no code change, or no selected tests."""
    baseline: BaselineReport | None = None
    probes: Sequence[ProbeResult] = ()

    def probe(self, name: ProbeName) -> ProbeResult | None:
        return next((p for p in self.probes if p.probe == name), None)


@dataclass(frozen=True)
class Rule:
    number: int
    rule_id: str
    verdict: VerdictName
    check: Callable[[DecisionInput, Config], list[str]]
    """Returns reasons when the rule matches, an empty list otherwise."""


def _build(inp: DecisionInput, _config: Config) -> list[str]:
    if inp.build_error:
        return [inp.build_error]
    if inp.baseline is not None and inp.baseline.status == "no_tests":
        return ["no selected tests for the changed lines"]
    return []


def _flaky(inp: DecisionInput, config: Config) -> list[str]:
    report = inp.baseline
    if report is None or report.flaky_ratio <= config.flaky.max_ratio:
        return []
    return [
        f"FLAKY_SUITE: {len(report.flaky)} of {len(report.selected)} selected tests are flaky "
        f"(ratio {report.flaky_ratio:.2f} > {config.flaky.max_ratio})"
    ]


def _claim(inp: DecisionInput, _config: Config) -> list[str]:
    audit = inp.probe("claim_audit")
    if audit is None:
        return []
    reasons = []
    if "CLAIM_FALSE_GREEN" in audit.signals:
        reasons.append("CLAIM_FALSE_GREEN: the agent claimed passing tests; the baseline fails")
    if "CLAIM_NO_RUN" in audit.signals and inp.baseline is not None and inp.baseline.failing:
        reasons.append("CLAIM_NO_RUN: pass claimed without running tests, and the baseline fails")
    return reasons


def _head_red(inp: DecisionInput, _config: Config) -> list[str]:
    report = inp.baseline
    if report is None or not report.failing:
        return []
    shown = ", ".join(report.failing[:5]) + (" ..." if len(report.failing) > 5 else "")
    return [f"HEAD_RED: {len(report.failing)} selected test(s) fail on head: {shown}"]


def _integrity(inp: DecisionInput, _config: Config) -> list[str]:
    integrity = inp.probe("integrity")
    if integrity is None:
        return []
    return [
        f"{f.rule_id} at {f.file}:{f.line}: {f.message}"
        for f in integrity.findings
        if f.severity == "high" and not f.waived and f.rule_id in REFUTING_INTEGRITY_RULES
    ]


RULES: Final[tuple[Rule, ...]] = (
    Rule(1, "R-INC-BUILD", "INCONCLUSIVE", _build),
    Rule(2, "R-INC-FLAKY", "INCONCLUSIVE", _flaky),
    Rule(3, "R-REF-CLAIM", "REFUTED", _claim),
    Rule(4, "R-INC-HEADRED", "INCONCLUSIVE", _head_red),
    Rule(5, "R-REF-INTEGRITY", "REFUTED", _integrity),
)


def _evidence_summary(inp: DecisionInput) -> list[str]:
    summary = []
    report = inp.baseline
    if report is not None:
        flaky = f"; {len(report.flaky)} flaky excluded" if report.flaky else ""
        stable = len(report.stable)
        summary.append(
            f"baseline green: {stable} stable selected tests x {report.runs} runs{flaky}"
        )
    for probe in inp.probes:
        if probe.status == "skipped" and not probe.signals:
            continue
        metrics = ", ".join(f"{k}={v:g}" for k, v in sorted(probe.metrics.items()))
        summary.append(
            f"{probe.probe}: {probe.status} {'/'.join(probe.signals) or '-'}"
            + (f" ({metrics})" if metrics else "")
        )
    return summary


def decide(inp: DecisionInput, config: Config) -> Verdict:
    """Section 8, in order. Pure: same inputs always give the same verdict."""
    first: Rule | None = None
    reasons: list[str] = []
    for rule in RULES:
        matched = rule.check(inp, config)
        if matched:
            reasons += [f"[{rule.rule_id}] {r}" for r in matched]
            first = first or rule
    if first is not None:
        return Verdict.make(first.verdict, first.rule_id, reasons)
    return Verdict.make(
        "INCONCLUSIVE",
        NOT_IMPLEMENTED,
        _evidence_summary(inp),
        confidence_note=(
            "Rules 1-5 passed; mutant-based rules (6-14) arrive in M3, so no final verdict yet."
        ),
    )
