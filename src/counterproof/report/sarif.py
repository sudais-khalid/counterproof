"""SARIF 2.1.0 output (PRD section 14): one result per finding and per confirmed survivor.

Locations are repository-relative URIs, so GitHub code scanning shows results inline.
Findings without a source location (claim audit, blind oracle) are attached to the first
changed source file with ``properties.approximateLocation = true``: code scanning rejects
results without a location. Waived integrity findings carry an ``inSource`` suppression.
"""

from __future__ import annotations

import json
from typing import Any, Final

from counterproof import __version__
from counterproof.models import Finding, Mutant
from counterproof.report.bundle import RunBundle

SCHEMA: Final = "https://json.schemastore.org/sarif-2.1.0.json"
INFO_URI: Final = "https://github.com/sudais-khalid/counterproof"
SURVIVOR_RULE: Final = "MU-SURVIVED"
LEVELS: Final = {"high": "error", "medium": "warning", "low": "note"}
DESCRIPTIONS: Final[dict[str, str]] = {
    "FR-IN-01": "Assertion removed from an existing test",
    "FR-IN-02": "Test deleted or renamed away",
    "FR-IN-03": "Skip or expected-failure marker added",
    "FR-IN-04": "Expected value loosened",
    "FR-IN-05": "Assertion neutralised",
    "FR-IN-06": "Unit under test mocked",
    "FR-IN-07": "Code special-cases test inputs (overfit)",
    "FR-IN-08": "Code detects the test runner",
    "FR-IN-09": "Test-runner or Counterproof configuration changed",
    "CLAIM_FALSE_GREEN": "Agent claimed passing tests; the baseline fails",
    "CLAIM_NO_RUN": "Agent claimed passing tests without running them after the last edit",
    "CLAIM_COUNT_MISMATCH": "Claimed test count differs from the run",
    "CLAIM_SCOPE": "Claimed the full suite; ran a subset",
    "CLAIM_MASKED_EXIT": "Test command's exit code was hidden",
    "BO-LIKELY-BUG": "Blind-oracle test fails on the change (likely bug)",
    SURVIVOR_RULE: "A plausible wrong version of the changed code passes every test",
}


def _location(file: str | None, line: int | None) -> dict[str, Any]:
    physical: dict[str, Any] = {"artifactLocation": {"uri": file, "uriBaseId": "%SRCROOT%"}}
    if line:
        physical["region"] = {"startLine": line}
    return {"physicalLocation": physical}


def _finding_result(f: Finding, fallback: str | None, probe: str) -> dict[str, Any] | None:
    file = f.file if f.file and f.file != "transcript" else None
    approximate = file is None
    file = file or fallback
    if file is None:
        return None
    result: dict[str, Any] = {
        "ruleId": f.rule_id,
        "level": LEVELS.get(f.severity, "note"),
        "message": {"text": f.message},
        "locations": [_location(file, f.line if not approximate else 1)],
        "properties": {"probe": probe, "severity": f.severity},
    }
    if approximate:
        result["properties"]["approximateLocation"] = True
    if f.waived:
        result["suppressions"] = [
            {"kind": "inSource", "justification": f.waiver_reason or "waived"}
        ]
    return result


def _survivor_result(m: Mutant) -> dict[str, Any]:
    text = (
        f"{m.operator_or_class} mutant {m.id} survived: "
        f"`{' '.join(m.original.split())}` -> `{' '.join(m.replacement.split())}`"
    )
    if m.hint:
        text += f". Missing test: {m.hint}"
    return {
        "ruleId": SURVIVOR_RULE,
        "level": "warning",
        "message": {"text": text},
        "locations": [_location(m.file, m.start_line)],
        "properties": {
            "probe": "mutants",
            "mutantId": m.id,
            "source": m.source,
            "class": m.operator_or_class,
            **({"plausibility": m.plausibility} if m.plausibility else {}),
        },
    }


def render_sarif(bundle: RunBundle) -> str:
    manifest = bundle.manifest
    fallback = next(
        (f.path for f in manifest.change.files if f.role == "source" and f.status != "deleted"),
        next((f.path for f in manifest.change.files), None),
    )
    results: list[dict[str, Any]] = []
    for probe in manifest.probes:
        if probe.probe == "mutants":
            continue  # survivors come from mutants.json with their code and location
        for finding in probe.findings:
            result = _finding_result(finding, fallback, probe.probe)
            if result is not None:
                results.append(result)
    results += [_survivor_result(m) for m in bundle.mutants if m.status == "survived"]
    rule_ids = sorted({r["ruleId"] for r in results})
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "Counterproof",
                "version": __version__,
                "informationUri": INFO_URI,
                "rules": [
                    {
                        "id": rid,
                        "shortDescription": {"text": DESCRIPTIONS.get(rid, rid)},
                        "helpUri": f"{INFO_URI}/blob/main/docs/PRD.md",
                    }
                    for rid in rule_ids
                ],
            }
        },
        "automationDetails": {"id": f"counterproof/{manifest.run_id}"},
        "originalUriBaseIds": {"%SRCROOT%": {"uri": "./"}},
        "results": results,
        "properties": {
            "verdict": manifest.verdict.verdict,
            "verdictRule": manifest.verdict.rule_id,
            "reasons": manifest.verdict.reasons,
            "runId": manifest.run_id,
            "manifestSha256": bundle.manifest_sha256,
            "seedCommitment": manifest.seed_commitment,
        },
    }
    return json.dumps({"$schema": SCHEMA, "version": "2.1.0", "runs": [run]}, indent=2) + "\n"
