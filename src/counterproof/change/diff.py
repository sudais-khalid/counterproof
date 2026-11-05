"""Unified diff parsing (FR-CR-02).

Hunks come from ``git diff -U0`` so every hunk is exactly the changed lines; a separate
context window (default 20 lines) is cut from the head file for LLM prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from counterproof.models import FileStatus, Hunk

HUNK_RE: Final = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DEFAULT_CONTEXT_LINES: Final = 20


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    status: FileStatus
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)


def _strip_prefix(raw: str, prefix: str) -> str | None:
    raw = raw.rstrip("\n")
    # git appends a tab when a path has trailing whitespace; drop it.
    raw = raw.split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    return raw[len(prefix) :] if raw.startswith(prefix) else raw


def _header_paths(line: str) -> tuple[str, str] | None:
    """Paths from ``diff --git a/x b/y`` (used when there are no ---/+++ lines)."""
    body = line[len("diff --git ") :]
    if body.startswith("a/") and " b/" in body:
        # For identical old/new names the split point is unambiguous at the midpoint.
        half = (len(body) - 1) // 2
        left, right = body[:half], body[half + 1 :]
        if left[2:] == right[2:]:
            return left[2:], right[2:]
        old, _, new = body.partition(" b/")
        return old[2:], new
    return None


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse ``git diff -U0 -M --src-prefix=a/ --dst-prefix=b/`` output."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            paths = _header_paths(line) or ("", "")
            current = FileDiff(path=paths[1], old_path=paths[0], status="modified")
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is None:
            if line.startswith("new file mode"):
                current.status = "added"
            elif line.startswith("deleted file mode"):
                current.status = "deleted"
            elif line.startswith("rename from "):
                current.status = "renamed"
                current.old_path = line[len("rename from ") :]
            elif line.startswith("rename to "):
                current.path = line[len("rename to ") :]
            elif line.startswith("Binary files ") or line == "GIT binary patch":
                current.binary = True
            elif line.startswith("--- "):
                old = _strip_prefix(line[4:], "a/")
                if old is not None:
                    current.old_path = old
            elif line.startswith("+++ "):
                new = _strip_prefix(line[4:], "b/")
                if new is not None:
                    current.path = new
        match = HUNK_RE.match(line)
        if match:
            old_start, old_len, new_start, new_len = match.groups()
            hunk = Hunk(
                path=current.path,
                old_start=int(old_start),
                old_len=1 if old_len is None else int(old_len),
                new_start=int(new_start),
                new_len=1 if new_len is None else int(new_len),
            )
            current.hunks.append(hunk)
            continue
        if hunk is not None and line[:1] in {"+", "-"}:
            hunk.lines.append(line)
    for fd in files:
        if fd.status == "deleted":
            fd.path = fd.old_path or fd.path
        if fd.status != "renamed":
            fd.old_path = None
        for h in fd.hunks:
            h.path = fd.path
    return files


def changed_new_lines(hunk: Hunk) -> list[int]:
    """Head line numbers touched by a hunk.

    A pure deletion (``new_len == 0``) touches no head line, so the lines on either side of
    the gap are returned instead: tests covering them are the ones that could notice.
    """
    if hunk.new_len > 0:
        return list(range(hunk.new_start, hunk.new_start + hunk.new_len))
    return [n for n in (hunk.new_start, hunk.new_start + 1) if n > 0]


def context_window(
    head_text: str, hunk: Hunk, lines: int = DEFAULT_CONTEXT_LINES
) -> tuple[int, str]:
    """``(first_line_number, text)`` of the head file around ``hunk`` for LLM prompts."""
    all_lines = head_text.splitlines(keepends=True)
    touched = changed_new_lines(hunk) or [1]
    first = max(1, min(touched) - lines)
    last = min(len(all_lines), max(touched) + lines)
    return first, "".join(all_lines[first - 1 : last])
