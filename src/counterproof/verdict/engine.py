"""Exit-code policy (PRD section 11): ``--fail-on`` maps verdicts to a CI result. Pure."""

from __future__ import annotations

from typing import Final, Literal

from counterproof.models import Verdict, VerdictName

FailOn = Literal["weak", "unproven", "refuted"]
SEVERITY: Final[dict[VerdictName, int]] = {"PROVEN": 0, "WEAK": 1, "UNPROVEN": 2, "REFUTED": 3}
THRESHOLD: Final[dict[FailOn, int]] = {"weak": 1, "unproven": 2, "refuted": 3}


def exit_code_for(verdict: Verdict, fail_on: FailOn | None) -> int:
    """Without ``--fail-on`` the verdict's own code (0/10/20/30/40) is returned.

    With it, verdicts at or above the threshold keep their code and everything below exits 0.
    INCONCLUSIVE exits 0 under ``--fail-on``: the run could not judge the change, which the
    certificate states; it is not evidence against the change.
    """
    if fail_on is None:
        return verdict.exit_code
    if verdict.verdict == "INCONCLUSIVE":
        return 0
    return verdict.exit_code if SEVERITY[verdict.verdict] >= THRESHOLD[fail_on] else 0
