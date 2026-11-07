"""P5 Claim audit probe (PRD section 7.9, FR-CA-01 to FR-CA-05).

Pure over its inputs: a parsed transcript and the head baseline's failing tests. It runs
before the early verdict so rule 3 (R-REF-CLAIM) can see it, and also when the head is red
(FR-IS-05), since that is exactly when a "tests pass" claim matters most.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from counterproof.models import Finding, ProbeResult, Severity
from counterproof.probes.claim_audit.extract import Claims, extract_claims
from counterproof.probes.claim_audit.match import (
    CLAIM_FALSE_GREEN,
    CLAIM_NO_RUN,
    TestRun,
    contradictions,
    find_test_runs,
    last_edit,
)
from counterproof.probes.claim_audit.parsers.base import Transcript

CA_NO_TRANSCRIPT: Final = "CA_NO_TRANSCRIPT"
CA_UNREADABLE: Final = "CA_UNREADABLE"
CA_NO_FINAL_MESSAGE: Final = "CA_NO_FINAL_MESSAGE"
CA_NO_PASS_CLAIM: Final = "CA_NO_PASS_CLAIM"  # noqa: S105 - a signal name
CA_CONSISTENT: Final = "CA_CONSISTENT"
CA_AMBIGUOUS: Final = "CA_AMBIGUOUS"


@dataclass(frozen=True)
class ClaimAudit:
    result: ProbeResult
    evidence: dict[str, Any] = field(default_factory=dict)
    """Written to ``claims.json``: claims, test runs, last edit, contradictions."""


def _severity(signal: str, baseline_failing: Sequence[str]) -> Severity:
    # FR-CA-05: these two refute the change; the rest are WEAK-level.
    if signal == CLAIM_FALSE_GREEN or (signal == CLAIM_NO_RUN and baseline_failing):
        return "high"
    return "medium"


def _run_json(run: TestRun) -> dict[str, Any]:
    return {
        "line": run.line,
        "command": run.command,
        "runner": run.runner,
        "timestamp": run.timestamp,
        "exit_code": run.exit_code,
        "masked": run.masked,
        "full_suite": run.full_suite,
        "passed": run.passed,
        "failed": run.failed,
    }


def _claims_json(claims: Claims) -> dict[str, Any]:
    return {
        "pass_claim": claims.pass_claim,
        "claimed_passed": claims.claimed_passed,
        "full_suite": claims.full_suite,
        "files": claims.files,
        "completion": claims.completion,
        "quotes": claims.quotes,
        "ambiguous": claims.ambiguous,
    }


def run_claim_audit(
    transcript: Transcript | None,
    *,
    transcript_given: bool,
    baseline_failing: Sequence[str] | None,
) -> ClaimAudit:
    """``baseline_failing`` is None when no baseline ran (build error): no FALSE_GREEN check."""
    if not transcript_given:
        return ClaimAudit(
            ProbeResult(probe="claim_audit", status="skipped", signals=[CA_NO_TRANSCRIPT])
        )
    if transcript is None:
        return ClaimAudit(
            ProbeResult(probe="claim_audit", status="skipped", signals=[CA_UNREADABLE])
        )
    if not transcript.final_message:
        return ClaimAudit(
            ProbeResult(probe="claim_audit", status="skipped", signals=[CA_NO_FINAL_MESSAGE]),
            {"format": transcript.format},
        )

    claims = extract_claims(transcript.final_message)
    runs = find_test_runs(transcript.commands)
    edit_index, edit_line, edit_time = last_edit(transcript)
    failing = list(baseline_failing or [])
    found = contradictions(claims, runs, edit_index, baseline_failing)

    signals = [c.signal for c in found]
    if not claims.pass_claim:
        signals.append(CA_NO_PASS_CLAIM)
    elif not found:
        signals.append(CA_CONSISTENT)
    if claims.ambiguous:
        signals.append(CA_AMBIGUOUS)
    ref = f"transcript:{transcript.final_message_line}"
    findings = [
        Finding(
            rule_id=c.signal,
            severity=_severity(c.signal, failing),
            message=c.message,
            evidence_refs=[ref, *([f"transcript:{c.line}"] if c.line else []), "claims.json"],
        )
        for c in found
    ]
    after = [r for r in runs if r.index > edit_index]
    metrics: dict[str, float] = {
        "test_commands": float(len(runs)),
        "runs_after_last_edit": float(len(after)),
        "edits": float(len(transcript.edits)),
    }
    if claims.claimed_passed is not None:
        metrics["claimed_passed"] = float(claims.claimed_passed)
    if after and after[-1].passed is not None:
        metrics["observed_passed"] = float(after[-1].passed)
        metrics["observed_failed"] = float(after[-1].failed or 0)
    evidence = {
        "format": transcript.format,
        "author_models": transcript.author_models,
        "final_message_line": transcript.final_message_line,
        "claims": _claims_json(claims),
        "test_runs": [_run_json(r) for r in runs],
        "last_edit": {"line": edit_line, "timestamp": edit_time} if edit_index >= 0 else None,
        "baseline_failing": failing if baseline_failing is not None else None,
        "contradictions": [{"signal": c.signal, "message": c.message} for c in found],
    }
    return ClaimAudit(
        ProbeResult(
            probe="claim_audit", status="ok", signals=signals, findings=findings, metrics=metrics
        ),
        evidence,
    )
