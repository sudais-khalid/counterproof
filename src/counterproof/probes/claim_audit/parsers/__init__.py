"""Transcript parsers (FR-CA-01). Each turns one agent's session log into a :class:`Transcript`.

Parsers are tried in order; the first whose ``sniff`` accepts the file wins. Codex CLI and
Cursor parsers plug in here when they exist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from counterproof.probes.claim_audit.parsers import claude_code
from counterproof.probes.claim_audit.parsers.base import Transcript

Parser = tuple[str, Callable[[Sequence[str]], bool], Callable[[Sequence[str]], Transcript]]
PARSERS: Final[tuple[Parser, ...]] = (("claude_code", claude_code.sniff, claude_code.parse),)
MAX_TRANSCRIPT_BYTES: Final = 64 * 1024 * 1024


def load_transcript(path: Path) -> Transcript | None:
    """The parsed transcript, or None when the file is unreadable or no parser knows it."""
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for _name, sniff, parse in PARSERS:
        if sniff(lines):
            return parse(lines)
    return None


__all__ = ["PARSERS", "Transcript", "load_transcript"]
