"""BaselineRunner: repeated head runs, flaky detection and timings (FR-IS-03 to FR-IS-06).

:func:`analyse_baseline` is pure so flakiness rules are unit-tested without running pytest.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from counterproof.models import BaselineReport, BaselineStatus, RunResult, TestOutcome

FAILING: Final[frozenset[TestOutcome]] = frozenset({"fail", "error", "timeout"})
MUTANT_TIMEOUT_FACTOR: Final = 3.0
MUTANT_TIMEOUT_PAD_S: Final = 5.0


def analyse_baseline(
    runs: Sequence[RunResult],
    selected: Sequence[str],
    *,
    known_flaky: Sequence[str] = (),
    max_ratio: float = 0.2,
) -> BaselineReport:
    """Classify each selected test from ``runs`` head runs.

    * FR-IS-03: a test is flaky when its outcome differs between runs. Flaky tests and the
      user's known-flaky list are excluded from every probe.
    * FR-IS-04: more than ``max_ratio`` of the (not known-flaky) selection flaky gives
      ``flaky_suite``. Known-flaky tests do not count: the user already declared them.
    * FR-IS-05: any other selected test failing on head gives ``head_red``.
    """
    known = sorted(set(known_flaky) & set(selected))
    outcomes: dict[str, list[TestOutcome]] = {}
    durations: dict[str, float] = {}
    for test in selected:
        outcomes[test] = [run.tests.get(test, "error") for run in runs]
        # FR-IS-06: the slowest observed run is the safe basis for later timeouts.
        durations[test] = max((run.test_durations.get(test, 0.0) for run in runs), default=0.0)
    candidates = [t for t in selected if t not in set(known)]
    flaky = sorted(t for t in candidates if len(set(outcomes[t])) > 1)
    failing = sorted(t for t in candidates if t not in set(flaky) and set(outcomes[t]) & FAILING)
    ratio = len(flaky) / len(candidates) if candidates else 0.0

    status: BaselineStatus
    if not selected:
        status = "no_tests"
    elif ratio > max_ratio:
        status = "flaky_suite"
    elif failing:
        status = "head_red"
    else:
        status = "ok"
    return BaselineReport(
        runs=len(runs),
        status=status,
        selected=list(selected),
        flaky=flaky,
        known_flaky=known,
        failing=failing,
        flaky_ratio=round(ratio, 6),
        durations=durations,
        outcomes=outcomes,
    )


def mutant_timeout_s(report: BaselineReport, tests: Sequence[str]) -> float:
    """FR-IS-06: ``3 x baseline duration of the tests to run + 5 s``."""
    baseline = sum(report.durations.get(t, 0.0) for t in tests)
    return MUTANT_TIMEOUT_FACTOR * baseline + MUTANT_TIMEOUT_PAD_S
