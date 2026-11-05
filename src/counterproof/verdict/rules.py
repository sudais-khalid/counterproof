"""Verdict rules (PRD section 8): a pure function from typed results to a :class:`Verdict`.

Rules are evaluated top to bottom. The first matching rule sets the verdict and rule id;
every matching rule contributes reasons, in rule order. No I/O, no clock, no randomness.

Rule 14 (R-PROVEN) applies only when nothing above matched *and* its own conditions hold.
When they do not (in practice: a bugfix or feature whose tests kill the mutants but showed no
red-green evidence), the verdict is WEAK with rule id ``R-WEAK-NOREDGREEN``.
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

# FR-IN rules whose high-severity findings refute a change (rule 5).
REFUTING_INTEGRITY_RULES: Final = frozenset(
    {"FR-IN-01", "FR-IN-02", "FR-IN-03", "FR-IN-05", "FR-IN-07", "FR-IN-08"}
)
# FR-CA-05: claim contradictions that are WEAK-level (rule 12).
WEAK_CLAIM_SIGNALS: Final = frozenset(
    {"CLAIM_COUNT_MISMATCH", "CLAIM_SCOPE", "CLAIM_MASKED_EXIT", "CLAIM_NO_RUN"}
)
NO_RED_GREEN: Final = frozenset({"RG_PASS_BOTH", "RG_NO_CANDIDATES"})
MIN_MUTANTS: Final = 3
EARLY_RULES: Final = 5
"""Rules 1-5 depend only on the baseline, P2 and P5: a match there is final."""


@dataclass(frozen=True)
class DecisionInput:
    intent: Intent
    build_error: str | None = None
    """Rule 1: prepare/collect failure, no code change, or no selected tests."""
    baseline: BaselineReport | None = None
    probes: Sequence[ProbeResult] = ()
    llm_enabled: bool = False

    def probe(self, name: ProbeName) -> ProbeResult | None:
        return next((p for p in self.probes if p.probe == name), None)

    def signals(self, name: ProbeName) -> list[str]:
        probe = self.probe(name)
        return probe.signals if probe else []

    @property
    def kill_ratio(self) -> float | None:
        mutants = self.probe("mutants")
        return mutants.metrics.get("kill_ratio") if mutants else None

    @property
    def valid_mutants(self) -> int:
        mutants = self.probe("mutants")
        return int(mutants.metrics.get("valid", 0)) if mutants else 0

    @property
    def killed_mutants(self) -> int:
        mutants = self.probe("mutants")
        return int(mutants.metrics.get("killed", 0)) if mutants else 0


@dataclass(frozen=True)
class Rule:
    number: int
    rule_id: str
    verdict: VerdictName
    check: Callable[[DecisionInput, Config], list[str]]
    """Returns reasons when the rule matches, an empty list otherwise."""


def _ratio(inp: DecisionInput) -> str:
    ratio = inp.kill_ratio
    if ratio is None:
        return "no valid mutants"
    return f"{inp.killed_mutants}/{inp.valid_mutants} mutants killed ({ratio:.2f})"


# -- rules 1-5: the run itself and gaming evidence ----------------------------------------


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
    signals = inp.signals("claim_audit")
    reasons = []
    if "CLAIM_FALSE_GREEN" in signals:
        reasons.append("CLAIM_FALSE_GREEN: the agent claimed passing tests; the baseline fails")
    if "CLAIM_NO_RUN" in signals and inp.baseline is not None and inp.baseline.failing:
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


# -- rules 6-13: strength of the evidence --------------------------------------------------


def _overfit(inp: DecisionInput, _config: Config) -> list[str]:
    if "MU_OVERFIT_SURVIVED" in inp.signals("mutants"):
        return ["MU_OVERFIT_SURVIVED: a version correct only for the visible test inputs passes"]
    return []


def _no_red_green(inp: DecisionInput, config: Config) -> list[str]:
    rg = set(inp.signals("red_green")) & NO_RED_GREEN
    ratio = inp.kill_ratio if inp.kill_ratio is not None else 0.0
    if inp.intent in {"bugfix", "feature"} and rg and ratio < config.mutants.proven_at_least:
        return [f"{sorted(rg)[0]}: the change's tests pass on the old code too; {_ratio(inp)}"]
    return []


def _weak_tests(inp: DecisionInput, config: Config) -> list[str]:
    ratio = inp.kill_ratio
    if inp.intent == "test_only" and inp.valid_mutants >= 1 and inp.killed_mutants == 0:
        return [f"the new tests kill none of the faults in the code they cover ({_ratio(inp)})"]
    if (
        ratio is not None
        and inp.valid_mutants >= MIN_MUTANTS
        and ratio < config.mutants.unproven_below
    ):
        return [f"most wrong versions of the changed lines pass the tests: {_ratio(inp)}"]
    return []


def _survivor(inp: DecisionInput, config: Config) -> list[str]:
    mutants = inp.probe("mutants")
    reasons = []
    high = int(mutants.metrics.get("high_plausibility_survivors", 0)) if mutants else 0
    if high:
        reasons.append(f"{high} high-plausibility mutant(s) survived")
    ratio = inp.kill_ratio
    if (
        ratio is not None
        and config.mutants.unproven_below <= ratio < config.mutants.proven_at_least
    ):
        reasons.append(f"some realistic wrong versions survive: {_ratio(inp)}")
    return reasons


def _api_only(inp: DecisionInput, _config: Config) -> list[str]:
    ratio = inp.kill_ratio
    if "RG_API_ONLY" in inp.signals("red_green") and (ratio is None or ratio < 1.0):
        return [
            f"RG_API_ONLY: tests only fail on the old code because the API is absent; {_ratio(inp)}"
        ]
    return []


def _oracle(inp: DecisionInput, _config: Config) -> list[str]:
    oracle = inp.probe("blind_oracle")
    if oracle is None:
        return []
    return [f.message for f in oracle.findings if f.rule_id == "BO-LIKELY-BUG" and not f.waived]


def _medium(inp: DecisionInput, _config: Config) -> list[str]:
    reasons: list[str] = []
    integrity = inp.probe("integrity")
    if integrity is not None:
        reasons += [
            f"{f.rule_id}" + (f" at {f.file}:{f.line}" if f.file else "") + f": {f.message}"
            for f in integrity.findings
            if f.severity == "medium" and not f.waived
        ]
    reasons += [f"{s} (claim audit)" for s in inp.signals("claim_audit") if s in WEAK_CLAIM_SIGNALS]
    return reasons


def _few_mutants(inp: DecisionInput, _config: Config) -> list[str]:
    if inp.valid_mutants < MIN_MUTANTS:
        return [
            f"only {inp.valid_mutants} valid mutant(s) could be generated "
            f"(need {MIN_MUTANTS}): not enough evidence"
        ]
    return []


RULES: Final[tuple[Rule, ...]] = (
    Rule(1, "R-INC-BUILD", "INCONCLUSIVE", _build),
    Rule(2, "R-INC-FLAKY", "INCONCLUSIVE", _flaky),
    Rule(3, "R-REF-CLAIM", "REFUTED", _claim),
    Rule(4, "R-INC-HEADRED", "INCONCLUSIVE", _head_red),
    Rule(5, "R-REF-INTEGRITY", "REFUTED", _integrity),
    Rule(6, "R-UNP-OVERFIT", "UNPROVEN", _overfit),
    Rule(7, "R-UNP-NOREDGREEN", "UNPROVEN", _no_red_green),
    Rule(8, "R-UNP-WEAKTESTS", "UNPROVEN", _weak_tests),
    Rule(9, "R-WEAK-SURVIVOR", "WEAK", _survivor),
    Rule(10, "R-WEAK-APIONLY", "WEAK", _api_only),
    Rule(11, "R-WEAK-ORACLE", "WEAK", _oracle),
    Rule(12, "R-WEAK-MEDIUM", "WEAK", _medium),
    Rule(13, "R-WEAK-FEWMUTANTS", "WEAK", _few_mutants),
)


def confidence_note(inp: DecisionInput) -> str:
    """What the verdict rests on, and what was not checked (PRD section 21)."""
    parts = []
    valid = inp.valid_mutants
    if valid:
        parts.append(f"Tested against {valid} plausible fault(s) on the changed lines")
    if not inp.llm_enabled:
        parts.append("LLM disabled: rule mutants only, no blind oracle")
    elif inp.probe("blind_oracle") is None:
        parts.append("blind oracle not run")
    if inp.probe("claim_audit") is None:
        parts.append("claim audit not run")
    parts.append("not a proof of full program correctness")
    return "; ".join(parts) + "."


def _proven(inp: DecisionInput) -> tuple[bool, str]:
    """Rule 14's own conditions: red-green evidence (or an intent that does not need it)."""
    rg = inp.signals("red_green")
    if inp.intent in {"refactor", "test_only"}:
        return True, f"intent {inp.intent}: proof rests on the mutants; {_ratio(inp)}"
    if "RG_STRONG" in rg:
        return True, f"tests fail on the old code and pass on the new; {_ratio(inp)}"
    if "RG_API_ONLY" in rg:  # rule 10 did not fire, so every mutant was killed
        return True, f"new API with every mutant killed; {_ratio(inp)}"
    shown = "/".join(rg) or "red-green not run"
    return False, f"no red-green evidence ({shown}) although {_ratio(inp)}"


def early_verdict(inp: DecisionInput, config: Config) -> Verdict | None:
    """Rules 1-5 only. A match here is final, so expensive probes can be skipped."""
    return _first_match(inp, config, RULES[:EARLY_RULES])


def _first_match(inp: DecisionInput, config: Config, rules: Sequence[Rule]) -> Verdict | None:
    first: Rule | None = None
    reasons: list[str] = []
    for rule in rules:
        matched = rule.check(inp, config)
        if matched:
            reasons += [f"[{rule.rule_id}] {r}" for r in matched]
            first = first or rule
    if first is None:
        return None
    note = "" if first.verdict in {"INCONCLUSIVE", "REFUTED"} else confidence_note(inp)
    return Verdict.make(first.verdict, first.rule_id, reasons, confidence_note=note)


def decide(inp: DecisionInput, config: Config) -> Verdict:
    """Section 8, in order. Pure: same inputs always give the same verdict."""
    matched = _first_match(inp, config, RULES)
    if matched is not None:
        return matched
    ok, reason = _proven(inp)
    if ok:
        return Verdict.make(
            "PROVEN", "R-PROVEN", [f"[R-PROVEN] {reason}"], confidence_note=confidence_note(inp)
        )
    return Verdict.make(
        "WEAK",
        "R-WEAK-NOREDGREEN",
        [f"[R-WEAK-NOREDGREEN] {reason}"],
        confidence_note=confidence_note(inp),
    )
