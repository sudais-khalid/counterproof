"""Inline waivers: ``# counterproof: allow <rule_id> <reason>`` (PRD section 7.6).

A waiver sits on the finding's line or the line directly above it. Waivers are always listed
on the certificate. A waiver that the change under test *adds* does not apply: the agent
being verified must not be able to excuse its own violation (the same reasoning as reading
``counterproof.toml`` from the base commit, PRD 15.3). It is recorded as a proposed waiver
for a human to accept by committing it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from counterproof.models import Finding

WAIVER_RE: Final = re.compile(r"counterproof:\s*allow\s+(FR-IN-\d{2})\b\s*(.*)$", re.IGNORECASE)
PROPOSED_NOTE: Final = "proposed waiver added by this change (not applied)"

LineReader = Callable[[str], list[str] | None]
AddedLines = Callable[[str], set[int]]


def _waiver_at(lines: list[str], line: int, rule_id: str) -> tuple[int, str] | None:
    for candidate in (line, line - 1):
        if 0 < candidate <= len(lines):
            match = WAIVER_RE.search(lines[candidate - 1])
            if match and match.group(1).upper() == rule_id:
                return candidate, match.group(2).strip() or "no reason given"
    return None


def apply_waivers(
    findings: list[Finding],
    *,
    head_lines: LineReader,
    base_lines: LineReader,
    added_lines: AddedLines,
) -> list[Finding]:
    """Mark waived findings. FR-IN-02 (deleted tests) looks in the base file; others in head."""
    result: list[Finding] = []
    for f in findings:
        if f.file is None or f.line is None:
            result.append(f)
            continue
        in_base = f.rule_id == "FR-IN-02"
        lines = (base_lines if in_base else head_lines)(f.file)
        waiver = _waiver_at(lines, f.line, f.rule_id) if lines else None
        if waiver is None:
            result.append(f)
            continue
        waiver_line, reason = waiver
        if not in_base and waiver_line in added_lines(f.file):
            result.append(
                f.model_copy(
                    update={"evidence_refs": [*f.evidence_refs, f"{PROPOSED_NOTE}: {reason}"]}
                )
            )
        else:
            result.append(f.model_copy(update={"waived": True, "waiver_reason": reason}))
    return result
