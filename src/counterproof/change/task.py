"""Task text resolution (FR-CR-04).

Priority: ``--task``, ``--task-file``, the first user message of a Claude Code transcript,
the pull request title and body (GitHub Actions), the last commit message.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

TaskSource = Literal["cli", "file", "transcript", "pull_request", "commit", "none"]
MAX_TASK_CHARS: Final = 20_000


@dataclass(frozen=True)
class TaskText:
    text: str | None
    source: TaskSource


def _text_blocks(content: Any) -> str | None:
    """Text of a message ``content`` that is either a string or a list of typed blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    texts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    # A list holding only tool results is not something the user typed.
    return "\n".join(texts) if texts else None


def first_user_message(transcript: Path) -> str | None:
    """First user-typed message in a Claude Code session JSONL file.

    Tolerant by design: unknown entry types, malformed lines, sidechain (subagent) entries and
    tool-result-only user entries are skipped.
    """
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role", "user") != "user":
            continue
        text = _text_blocks(message.get("content"))
        if text and text.strip():
            return text.strip()
    return None


def pull_request_text(env: Mapping[str, str]) -> str | None:
    """Title and body from the GitHub Actions event payload, if this is a PR event."""
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pr = event.get("pull_request") if isinstance(event, dict) else None
    if not isinstance(pr, dict):
        return None
    parts = [p for p in (pr.get("title"), pr.get("body")) if isinstance(p, str) and p.strip()]
    return "\n\n".join(parts) or None


def resolve_task_text(
    *,
    cli_text: str | None,
    file_text: str | None,
    transcript: Path | None,
    commit_message: str | None,
    env: Mapping[str, str] | None = None,
) -> TaskText:
    env = os.environ if env is None else env
    candidates: list[tuple[TaskSource, str | None]] = [
        ("cli", cli_text),
        ("file", file_text),
        ("transcript", first_user_message(transcript) if transcript else None),
        ("pull_request", pull_request_text(env)),
        ("commit", commit_message),
    ]
    for source, text in candidates:
        if text and text.strip():
            return TaskText(text[:MAX_TASK_CHARS], source)
    return TaskText(None, "none")
