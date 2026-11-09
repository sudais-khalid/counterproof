"""Triage of failing oracle tests (FR-BO-04).

The judge sees the requirement, the oracle test, the failure on head, the outcome on base
and the head source of the changed functions the test calls. Only ``likely_bug`` with
confidence >= 0.8 becomes a finding. At most three calls per run (PRD section 16).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from counterproof.llm.roles import LLMSession
from counterproof.llm.schemas import TriageOutput
from counterproof.probes.blind_oracle.runner import OracleResult

LIKELY_BUG_THRESHOLD: Final = 0.8
MAX_TRIAGE: Final = 3
MAX_FAILURE_CHARS: Final = 2000
MAX_CODE_CHARS: Final = 6000
_WINDOWS_PATH: Final = re.compile(r"[\w.-]+(?:\\[\w.-]+)+\.(?:py|[cm]?[jt]sx?)\b")
# pytest's "_ _ _" section separator follows the terminal width, which differs by platform.
_SEPARATOR: Final = re.compile(r"^(?:_ ){8,}_? *$", re.M)
_CANONICAL_SEPARATOR: Final = "_ " * 39 + "_"  # what a Windows console draws


def platform_neutral(text: str) -> str:
    """Same failure text on every OS: POSIX separators in paths, LF line endings, and one
    width for pytest's section separators.

    The prompt is part of the cache key, so a recorded answer must replay everywhere.
    """
    text = text.replace("\r\n", "\n")
    text = _SEPARATOR.sub(_CANONICAL_SEPARATOR, text)
    return _WINDOWS_PATH.sub(lambda m: m.group(0).replace("\\", "/"), text)


@dataclass(frozen=True)
class Triage:
    label: str
    confidence: float
    explanation: str

    @property
    def is_finding(self) -> bool:
        return self.label == "likely_bug" and self.confidence >= LIKELY_BUG_THRESHOLD


def _base_outcome(result: OracleResult) -> str:
    outcome = result.base_outcome
    if outcome == "pass":
        return "PASSED (so the change broke this behaviour)"
    if outcome in {"fail", "error"}:
        kind = result.base_failure.kind if result.base_failure else "failure"
        return f"also FAILED ({kind})"
    return "was not run"


def _code_for(test_code: str, functions: Mapping[str, str]) -> str:
    called = [
        src for name, src in functions.items() if re.search(rf"\b{re.escape(name)}\b", test_code)
    ]
    chosen = called or list(functions.values())
    return "\n\n".join(chosen)[:MAX_CODE_CHARS] or "# (source not available)"


def triage_failures(
    session: LLMSession,
    *,
    model: str,
    results: Sequence[OracleResult],
    task_text: str | None,
    functions: Mapping[str, str],
    language: str = "python",
) -> dict[str, Triage]:
    """``test name -> triage`` for the first :data:`MAX_TRIAGE` failing tests."""
    out: dict[str, Triage] = {}
    failing = [r for r in results if r.status == "failing"][:MAX_TRIAGE]
    for result in failing:
        failure = result.failure
        text = f"{failure.message}\n{failure.traceback_excerpt}" if failure else "(no output)"
        text = platform_neutral(text)
        verdict = session.run(
            "triage_judge",
            TriageOutput,
            {
                "requirement": f"{result.case.intent}\n\nTask: {task_text or '(none given)'}",
                "test": result.case.code,
                "failure": text.strip()[-MAX_FAILURE_CHARS:],
                "base_outcome": _base_outcome(result),
                "code": _code_for(result.case.code, functions),
            },
            model=model,
            max_tokens=600,
            variant=None if language == "python" else language,
        )
        if verdict is not None:
            out[result.case.name] = Triage(
                verdict.label, verdict.confidence, " ".join(verdict.explanation.split())[:400]
            )
    return out
