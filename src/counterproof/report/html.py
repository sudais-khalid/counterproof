"""HTML report (PRD section 14): the certificate plus side-by-side diffs for every mutant, a
timeline of this repository's runs and expandable logs. One self-contained file (inline CSS,
no scripts, no external assets), rendered with Jinja2 and autoescaping."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, PackageLoader, select_autoescape

from counterproof import __version__
from counterproof.evidence.store import MANIFEST_NAME
from counterproof.models import ProbeResult
from counterproof.report.bundle import RunBundle
from counterproof.report.markdown import PROBE_ROWS, _llm_line, _probe_cell

MAX_LOG_CHARS: Final = 200_000
MAX_TIMELINE: Final = 30
LOG_GLOBS: Final = ("run.log", "junit/*.log", "mutants/*.log", "oracle/*.log")


@dataclass(frozen=True)
class DiffRow:
    left_no: str
    left: str
    right_no: str
    right: str
    kind: str  # "same", "changed", "removed", "added"


def side_by_side(original: str, replacement: str, first_line: int) -> list[DiffRow]:
    old, new = original.splitlines(), replacement.splitlines()
    rows: list[DiffRow] = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        span = max(i2 - i1, j2 - j1)
        for k in range(span):
            left = old[i1 + k] if i1 + k < i2 else None
            right = new[j1 + k] if j1 + k < j2 else None
            kind = {"equal": "same", "delete": "removed", "insert": "added"}.get(tag, "changed")
            rows.append(
                DiffRow(
                    left_no=str(first_line + i1 + k) if left is not None else "",
                    left=left or "",
                    right_no=str(first_line + j1 + k) if right is not None else "",
                    right=right or "",
                    kind=kind,
                )
            )
    return rows


def _timeline(bundle: RunBundle) -> list[dict[str, Any]]:
    entries = []
    for run in sorted(bundle.run_dir.parent.iterdir(), reverse=True)[:MAX_TIMELINE]:
        path = run / MANIFEST_NAME
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        verdict = data.get("verdict", {})
        entries.append(
            {
                "run_id": data.get("run_id", run.name),
                "created": str(data.get("created_at", ""))[:19].replace("T", " "),
                "verdict": verdict.get("verdict", "?"),
                "rule": verdict.get("rule_id", ""),
                "current": run.name == bundle.manifest.run_id,
            }
        )
    return entries


def _logs(run_dir: Path) -> list[dict[str, str]]:
    found = []
    for pattern in LOG_GLOBS:
        for path in sorted(run_dir.glob(pattern)):
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_LOG_CHARS:
                text = text[:MAX_LOG_CHARS] + "\n... (truncated)"
            found.append({"name": path.relative_to(run_dir).as_posix(), "text": text})
    return found


def render_html(bundle: RunBundle) -> str:
    env = Environment(
        loader=PackageLoader("counterproof.report", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    manifest = bundle.manifest
    probes: dict[str, ProbeResult] = {p.probe: p for p in manifest.probes}
    findings = [(p.probe, f) for p in manifest.probes if p.probe != "mutants" for f in p.findings]
    mutants = [
        {"m": m, "rows": side_by_side(m.original, m.replacement, m.start_line)}
        for m in bundle.mutants
    ]
    return env.get_template("report.html.j2").render(
        manifest=manifest,
        version=__version__,
        manifest_sha256=bundle.manifest_sha256,
        profile=bundle.config.get("run", {}).get("profile", "?"),
        rows=[(label, _probe_cell(name, probes, bundle)) for label, name in PROBE_ROWS],
        findings=findings,
        mutants=mutants,
        timeline=_timeline(bundle),
        logs=_logs(bundle.run_dir),
        llm_line=_llm_line(bundle),
    )
