"""P2 Integrity probe (PRD section 7.6): static, fast, runs before any test execution."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Final

from counterproof.change.diff import changed_new_lines
from counterproof.change.self_config import SELF_CONFIG_FLAG
from counterproof.models import ChangeSet, Cost, Finding, ProbeResult
from counterproof.probes import lang
from counterproof.probes.integrity.overfit import env_detection_findings
from counterproof.probes.integrity.python_rules import (
    ChangedSource,
    TestFilePair,
    compare_test_files,
    finding,
)
from counterproof.probes.integrity.waivers import apply_waivers
from counterproof.probes.pyast import Literal

Reader = Callable[[str], str | None]
SNAPSHOT_SUFFIXES: Final = (".snap", ".ambr")


def changed_test_file_pairs(
    change: ChangeSet, read_base: Reader, read_head: Reader
) -> list[TestFilePair]:
    pairs: list[TestFilePair] = []
    for f in change.files:
        if f.role != "test" or not lang.supported(f.path) or f.binary:
            continue
        base_path = f.old_path or f.path
        pairs.append(
            TestFilePair(
                path=f.path,
                base_path=None if f.status == "added" else base_path,
                base_text=None if f.status == "added" else read_base(base_path),
                head_text=None if f.status == "deleted" else read_head(f.path),
            )
        )
    return pairs


def added_lines_by_file(change: ChangeSet) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    for hunk in change.hunks:
        if hunk.new_len > 0:
            added.setdefault(hunk.path, set()).update(changed_new_lines(hunk))
    return added


def run_integrity(
    change: ChangeSet,
    *,
    read_base: Reader,
    read_head: Reader,
    literal_test_files: Sequence[str],
    covering_by_file: Mapping[str, set[str]] | None,
) -> ProbeResult:
    """FR-IN-01 to FR-IN-09 plus waivers.

    ``literal_test_files`` are head test files whose literals count as "test inputs and
    expected values" for FR-IN-07 (the changed and selected test files).
    """
    started = time.monotonic()
    added = added_lines_by_file(change)
    pairs = changed_test_file_pairs(change, read_base, read_head)
    sources = [
        f
        for f in change.files
        if f.role == "source" and f.status != "deleted" and lang.supported(f.path) and not f.binary
    ]
    head_sources = {f.path: read_head(f.path) or "" for f in sources}
    changed_sources = [
        ChangedSource(
            path=path,
            module=lang.module_name(path),
            definitions=frozenset(lang.top_level_definitions(path, text)),
        )
        for path, text in head_sources.items()
    ]

    findings: list[Finding] = compare_test_files(pairs, changed_sources, covering_by_file)

    literals: set[Literal] = set()
    for path in sorted(set(literal_test_files) | {p.path for p in pairs if p.head_text}):
        text = read_head(path)
        analysed = lang.analyse_test_file(path, text) if text is not None else None
        if analysed is not None:
            literals |= analysed.literals
    for path, text in head_sources.items():
        lines = added.get(path, set())
        findings += lang.overfit_findings(path, text, lines, frozenset(literals))
        split = text.splitlines()
        findings += env_detection_findings(
            path, [(n, split[n - 1]) for n in sorted(lines) if 0 < n <= len(split)]
        )

    if SELF_CONFIG_FLAG in change.flags:
        findings.append(
            finding(
                "FR-IN-09",
                "the change edits test-runner or Counterproof configuration",
                file=None,
                line=None,
            )
        )
    snapshots = [
        f.path
        for f in change.files
        if f.path.endswith(SNAPSHOT_SUFFIXES) or "__snapshots__" in PurePosixPath(f.path).parts
    ]
    if snapshots and sources:
        findings.append(
            finding(
                "FR-IN-04",
                "snapshots updated in the same change as source: "
                + ", ".join(sorted(snapshots)[:5]),
                file=snapshots[0],
                line=None,
            )
        )

    def lines_of(reader: Reader) -> Callable[[str], list[str] | None]:
        def read(path: str) -> list[str] | None:
            text = reader(path)
            return text.splitlines() if text is not None else None

        return read

    findings = apply_waivers(
        findings,
        head_lines=lines_of(read_head),
        base_lines=lines_of(read_base),
        added_lines=lambda path: added.get(path, set()),
    )
    findings.sort(key=lambda f: (f.file or "", f.line or 0, f.rule_id))
    active = [f for f in findings if not f.waived]
    high = sum(1 for f in active if f.severity == "high")
    medium = sum(1 for f in active if f.severity == "medium")
    signals = ["IN_HIGH"] if high else []
    signals += ["IN_MEDIUM"] if medium else []
    return ProbeResult(
        probe="integrity",
        status="ok",
        signals=signals or ["IN_CLEAN"],
        findings=findings,
        metrics={
            "findings_high": float(high),
            "findings_medium": float(medium),
            "findings_waived": float(len(findings) - len(active)),
            "test_files_compared": float(len(pairs)),
        },
        cost=Cost(seconds=round(time.monotonic() - started, 6)),
    )
