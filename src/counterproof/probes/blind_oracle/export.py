"""``counterproof export-oracle`` (FR-BO-06): copy accepted oracle tests into the repository.

Oracle tests stay in the run directory until a human accepts them by name. Nothing is ever
overwritten: the exported module gets a run-specific name.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from counterproof.errors import UsageError
from counterproof.probes.blind_oracle.writer import OracleCase, OracleSuite

RECORD = Path("oracle") / "oracle.json"
ALL = "all"


def load_record(run_dir: Path, run_id: str) -> dict[str, Any]:
    path = run_dir / RECORD
    if not path.is_file():
        raise UsageError(f"run {run_id} has no blind-oracle tests (the probe did not run)")
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not record.get("tests"):
        raise UsageError(f"run {run_id} has no usable blind-oracle tests")
    return record


def describe(record: dict[str, Any]) -> list[str]:
    lines = []
    for test in record["tests"]:
        status = test["status"]
        triage = test.get("triage")
        if status == "failing" and triage:
            status += f": {triage['label']} {triage['confidence']:.2f}"
        elif status == "discarded" and test.get("reason"):
            status += f": {test['reason']}"
        lines.append(f"{test['name']}  [{status}]  {test['intent']}")
    return lines


def export(
    record: dict[str, Any], run_id: str, accept: Sequence[str], dest_dir: Path
) -> tuple[Path, list[str]]:
    usable = {t["name"]: t for t in record["tests"] if t["status"] != "discarded"}
    names = list(usable) if list(accept) == [ALL] else list(dict.fromkeys(accept))
    unknown = [n for n in names if n not in usable]
    if unknown:
        known = ", ".join(usable) or "none"
        raise UsageError(
            f"unknown or discarded oracle test(s) {', '.join(unknown)} (usable: {known})"
        )
    if not names:
        raise UsageError("no usable oracle tests to export")
    tag = run_id.rsplit("-", 1)[-1]
    language = record.get("language", "python")
    if language == "python":
        target = dest_dir / f"test_counterproof_oracle_{tag}.py"
    else:
        target = dest_dir / f"counterproof_oracle_{tag}{record.get('suffix', '.test.ts')}"
    if target.exists():
        raise UsageError(f"{target} already exists; remove it or choose another --dest")
    suite = OracleSuite(
        imports=list(record.get("imports", [])),
        cases=[
            OracleCase(name=n, intent=usable[n]["intent"], code=usable[n]["code"]) for n in names
        ],
        language=language,
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(
        suite.text(f"Blind-oracle tests accepted from Counterproof run {run_id}."),
        encoding="utf-8",
    )
    return target, names
