"""Coach-mode hint (PRD sections 5.3, 13.1, 15.3): what the agent is told, and only that.

The hint names the *gap class and location* and what kind of test is missing. It is built
from structured fields (operator class, file, line, signal names), never from free text that
could carry a mutant's code: no mutant ``original``/``replacement``, no LLM-written hint or
equivalence note (they describe a mutant's behaviour), no oracle test names, intents or
explanations, and never the seed. A later audit run uses a fresh seed, so the agent cannot
overfit to the faults it was coached on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from counterproof.models import Mutant, ProbeResult, RunManifest

MAX_SURVIVORS: Final = 3
MAX_CHARS: Final = 900

# FR-MU-13 template hints keyed by rule operator: generic, no mutant text.
_CLASS_HINTS: Final[dict[str, str]] = {
    "boundary": "add a test where the two sides of that comparison are equal",
    "relational": "add a test that takes the other branch of that comparison",
    "arithmetic": "add a test whose expected value depends on that exact arithmetic",
    "negation": "add a test for both outcomes of that condition",
    "constant": "add a test that pins that constant; a nearby value should fail",
    "return_value": "assert on the value returned there",
    "statement_deletion": "add a test that depends on the effect of that line",
    "argument_swap": "add a test that tells the argument order apart",
    "exception_swallow": "add a test that expects that error",
}
_INTEGRITY: Final[dict[str, str]] = {
    "FR-IN-01": "a test lost assertions",
    "FR-IN-02": "a test was deleted or renamed away",
    "FR-IN-03": "a test was skipped or marked as expected to fail",
    "FR-IN-04": "an assertion was loosened",
    "FR-IN-05": "an assertion was neutralised so it can never fail",
    "FR-IN-06": "a test now mocks the code it is meant to check",
    "FR-IN-07": "the changed code special-cases values that appear in the tests",
    "FR-IN-08": "the changed code checks whether it runs under a test runner",
    "FR-IN-09": "the change edits test-runner or Counterproof configuration",
}
_CLAIMS: Final[dict[str, str]] = {
    "CLAIM_FALSE_GREEN": "you said the tests pass, but selected tests fail on this change",
    "CLAIM_NO_RUN": "you said the tests pass, but no test command ran after your last edit",
    "CLAIM_COUNT_MISMATCH": "the pass count you reported differs from the run you executed",
    "CLAIM_SCOPE": "you claimed the full suite, but ran only part of it",
    "CLAIM_MASKED_EXIT": "your test command hid its exit code (a pipe or `|| true`)",
}


def _where(file: str | None, line: int | None) -> str:
    if file and line:
        return f"`{file}:{line}`"
    return f"`{file}`" if file else "the change"


def _probe(manifest: RunManifest, name: str) -> ProbeResult | None:
    return next((p for p in manifest.probes if p.probe == name), None)


def _survivor_sentences(mutants: Sequence[Mutant]) -> list[str]:
    survivors = [m for m in mutants if m.status == "survived"]
    out: list[str] = []
    for m in survivors[:MAX_SURVIVORS]:
        kind = m.operator_or_class if m.source == "rule" else "realistic"
        if m.source == "llm" and m.operator_or_class == "overfit":
            out.append(
                f"a version of the code at {_where(m.file, m.start_line)} that is only correct "
                "for the inputs your tests use still passes them; test other inputs"
            )
            continue
        hint = _CLASS_HINTS.get(
            m.operator_or_class, "add a test that would catch a wrong version there"
        )
        out.append(
            f"a {kind} change at {_where(m.file, m.start_line)} is not detected by any test: {hint}"
        )
    if len(survivors) > MAX_SURVIVORS:
        out.append(f"{len(survivors) - MAX_SURVIVORS} more undetected change(s)")
    return out


def coach_hint(manifest: RunManifest, mutants: Sequence[Mutant]) -> str:
    """One short paragraph for the agent (empty sentences are dropped)."""
    verdict = manifest.verdict
    sentences: list[str] = []

    integrity = _probe(manifest, "integrity")
    for f in (integrity.findings if integrity else [])[:3]:
        if f.waived or f.severity == "low":
            continue
        sentences.append(
            f"{_INTEGRITY.get(f.rule_id, f.rule_id)} ({f.rule_id}, {_where(f.file, f.line)})"
        )

    claims = _probe(manifest, "claim_audit")
    for signal in claims.signals if claims else []:
        if signal in _CLAIMS:
            sentences.append(_CLAIMS[signal])

    red_green = _probe(manifest, "red_green")
    rg = set(red_green.signals) if red_green else set()
    if rg & {"RG_PASS_BOTH", "RG_NO_CANDIDATES"} and verdict.verdict != "PROVEN":
        sentences.append(
            "no test fails on the code before your change, so nothing shows the fix is needed"
        )
    if "RG_API_ONLY" in rg and verdict.rule_id == "R-WEAK-APIONLY":
        sentences.append(
            "your tests only fail on the old code because the new API is missing; "
            "test its behaviour"
        )

    if verdict.verdict in {"WEAK", "UNPROVEN"}:
        sentences += _survivor_sentences(mutants)
    oracle = _probe(manifest, "blind_oracle")
    if oracle is not None and any(f.rule_id == "BO-LIKELY-BUG" for f in oracle.findings):
        sentences.append(
            "an independent check found a likely bug in the changed behaviour "
            "(details are for the reviewer)"
        )
    if verdict.rule_id == "R-WEAK-FEWMUTANTS":
        sentences.append("too few plausible faults could be generated to judge the tests")
    if verdict.verdict == "INCONCLUSIVE":
        baseline_failing = [r for r in verdict.reasons if "HEAD_RED" in r]
        sentences.append(
            "selected tests fail on this change"
            if baseline_failing
            else "no verdict could be reached"
        )

    head = f"Counterproof: {verdict.verdict}."
    body = "; ".join(dict.fromkeys(s for s in sentences if s))
    text = f"{head} {body[0].upper() + body[1:]}." if body else head
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 3] + "..."


def summary_line(manifest: RunManifest) -> str:
    """One line for PROVEN / INCONCLUSIVE (shown to the user, not a block)."""
    verdict = manifest.verdict
    mutants = _probe(manifest, "mutants")
    if verdict.verdict == "PROVEN" and mutants is not None:
        valid = int(mutants.metrics.get("valid", 0))
        killed = int(mutants.metrics.get("killed", 0))
        return (
            f"Counterproof: PROVEN ({verdict.rule_id}): the tests detect "
            f"{killed}/{valid} plausible faults."
        )
    return f"Counterproof: {verdict.verdict} ({verdict.rule_id})."
