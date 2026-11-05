"""A sealed run loaded back from its run directory, for every reporter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from counterproof.errors import UsageError
from counterproof.evidence.hashing import sha256_file
from counterproof.evidence.store import MANIFEST_NAME, RUN_ID_RE, EvidenceStore, runs_root
from counterproof.models import BaselineReport, Mutant, RunManifest


@dataclass(frozen=True)
class RunBundle:
    manifest: RunManifest
    manifest_sha256: str
    run_dir: Path
    mutants: list[Mutant] = field(default_factory=list)
    red_green_cases: list[dict[str, Any]] = field(default_factory=list)
    baseline: BaselineReport | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def mutant(self, mutant_id: str) -> Mutant | None:
        return next((m for m in self.mutants if m.id == mutant_id), None)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_bundle(run_dir: Path) -> RunBundle:
    store = EvidenceStore.open(run_dir)
    manifest = store.load_manifest()
    mutants_data = _json(run_dir / "mutants.json") or {}
    red_green = _json(run_dir / "red_green_cases.json") or {}
    baseline = _json(run_dir / "baseline.json")
    return RunBundle(
        manifest=manifest,
        manifest_sha256=sha256_file(run_dir / MANIFEST_NAME),
        run_dir=run_dir,
        mutants=[Mutant.model_validate(m) for m in mutants_data.get("mutants", [])],
        red_green_cases=list(red_green.get("cases", [])),
        baseline=BaselineReport.model_validate(baseline) if baseline else None,
        config=_json(run_dir / "config.json") or {},
    )


def find_run(run_id: str, runs_dir: Path | None = None) -> Path:
    """Locate ``<runs>/<repo-hash>/<run-id>`` for any repository."""
    if not RUN_ID_RE.match(run_id):
        raise UsageError(f"invalid run id {run_id!r}")
    root = runs_dir if runs_dir is not None else runs_root()
    matches = sorted(p for p in root.glob(f"*/{run_id}") if (p / MANIFEST_NAME).is_file())
    if not matches:
        raise UsageError(f"run {run_id} not found under {root}")
    return matches[0]
