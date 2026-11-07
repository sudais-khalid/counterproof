"""P4 Blind oracle probe (PRD section 7.8, FR-BO-01 to FR-BO-07).

Interface -> oracle writer (one call per oracle model) -> three head runs -> base run of the
failing tests -> triage. Oracle findings never refute on their own (FR-BO-05): a likely bug
only adds a ``BO-LIKELY-BUG`` finding, which rule 11 turns into a WEAK cap.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from counterproof.adapters.base import Adapter
from counterproof.llm.roles import LLMSession
from counterproof.models import Cost, Finding, ProbeResult, ProbeStatus
from counterproof.probes.blind_oracle.interface import Interface
from counterproof.probes.blind_oracle.runner import OracleResult, run_oracle
from counterproof.probes.blind_oracle.triage import Triage, triage_failures
from counterproof.probes.blind_oracle.writer import write_oracle_tests
from counterproof.workspace.manager import Workspace

BO_DISABLED: Final = "BO_DISABLED"
BO_NO_LLM: Final = "BO_NO_LLM"
BO_NO_INTERFACE: Final = "BO_NO_INTERFACE"
BO_NO_TESTS: Final = "BO_NO_TESTS"
BO_ALL_DISCARDED: Final = "BO_ALL_DISCARDED"
BO_AGREE: Final = "BO_AGREE"
BO_FAILURES: Final = "BO_FAILURES"
BO_LIKELY_BUG: Final = "BO_LIKELY_BUG"
LIKELY_BUG_RULE: Final = "BO-LIKELY-BUG"


@dataclass(frozen=True)
class OracleOutcome:
    result: ProbeResult
    test_file: str | None = None
    """The oracle test module as it ran (written to ``oracle/test_oracle.py``)."""
    record: dict[str, Any] = field(default_factory=dict)
    """Written to ``oracle/oracle.json``; ``export-oracle`` reads it."""


def skipped(signal: str) -> OracleOutcome:
    return OracleOutcome(ProbeResult(probe="blind_oracle", status="skipped", signals=[signal]))


def _test_json(result: OracleResult, triage: Triage | None) -> dict[str, Any]:
    return {
        "name": result.case.name,
        "intent": result.case.intent,
        "model": result.case.model,
        "status": result.status,
        "reason": result.reason,
        "head_outcomes": list(result.head_outcomes),
        "failure": result.failure.message[:500] if result.failure else None,
        "base_outcome": result.base_outcome,
        "triage": vars(triage) if triage else None,
        "code": result.case.code,
    }


def run_blind_oracle(
    *,
    session: LLMSession,
    models: Sequence[str],
    model_note: str,
    judge_model: str,
    k: int,
    task_text: str | None,
    interface: Interface,
    functions: Mapping[str, str],
    adapter: Adapter,
    head: Workspace,
    base: Workspace,
    run_id: str,
    junit_path: Callable[[str], Path],
    timeout_s: float,
) -> OracleOutcome:
    started = time.monotonic()
    first_call = len(session.calls)
    suite = write_oracle_tests(
        session, models=models, task_text=task_text, interface=interface, k=k
    )
    record: dict[str, Any] = {
        "models": list(models),
        "model_note": model_note,
        "judge_model": judge_model,
        "style_path": interface.style_path,
        "test_dir": interface.test_dir,
        "imports": suite.imports,
        "rejected": [{"name": n, "reason": r} for n, r in suite.rejected],
        "tests": [],
    }

    def cost() -> Cost:
        calls = session.calls[first_call:]
        return Cost(
            tokens_in=sum(c.tokens_in for c in calls),
            tokens_out=sum(c.tokens_out for c in calls),
            usd=round(sum(c.usd for c in calls), 6),
            seconds=round(time.monotonic() - started, 3),
        )

    if not suite.cases:
        metrics = {"written": 0.0, "rejected": float(len(suite.rejected))}
        result = ProbeResult(
            probe="blind_oracle",
            status="inconclusive",
            signals=[BO_NO_TESTS],
            metrics=metrics,
            cost=cost(),
        )
        return OracleOutcome(result, None, record)

    header = f"Blind-oracle tests from Counterproof run {run_id} (written from the task only)."
    rel, results = run_oracle(
        adapter,
        head=head,
        base=base,
        suite=suite,
        test_dir=interface.test_dir,
        header=header,
        junit_path=junit_path,
        timeout_s=timeout_s,
    )
    triage = triage_failures(
        session, model=judge_model, results=results, task_text=task_text, functions=functions
    )
    record["file"] = rel
    record["header"] = header
    record["tests"] = [_test_json(r, triage.get(r.case.name)) for r in results]

    findings = [
        Finding(
            rule_id=LIKELY_BUG_RULE,
            severity="medium",
            message=(
                f"oracle test {r.case.name} ({r.case.intent}) fails on the change: "
                f"{triage[r.case.name].explanation}"
            ),
            evidence_refs=["oracle/oracle.json", "oracle/test_oracle.py"],
        )
        for r in results
        if r.case.name in triage and triage[r.case.name].is_finding
    ]
    count = {s: sum(1 for r in results if r.status == s) for s in ("agree", "failing", "discarded")}
    labels = [t.label for t in triage.values()]
    metrics = {
        "written": float(len(suite.cases)),
        "rejected": float(len(suite.rejected)),
        "valid": float(count["agree"] + count["failing"]),
        "agree": float(count["agree"]),
        "failing": float(count["failing"]),
        "discarded": float(count["discarded"]),
        "likely_bug": float(len(findings)),
        "oracle_wrong": float(labels.count("oracle_wrong")),
        "ambiguous": float(labels.count("ambiguous")),
        "untriaged": float(count["failing"] - len(triage)),
    }
    status: ProbeStatus = "ok"
    if not count["agree"] and not count["failing"]:
        signals, status = [BO_ALL_DISCARDED], "inconclusive"
    else:
        signals = [BO_FAILURES if count["failing"] else BO_AGREE]
        if findings:
            signals.append(BO_LIKELY_BUG)
    result = ProbeResult(
        probe="blind_oracle",
        status=status,
        signals=signals,
        findings=findings,
        metrics=metrics,
        cost=cost(),
    )
    return OracleOutcome(result, suite.text(header), record)
