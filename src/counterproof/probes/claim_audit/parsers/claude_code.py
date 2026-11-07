"""Claude Code session JSONL (FR-CA-01).

Checked against real session files (Claude Code, November 2026): one JSON object per line;
``type`` is ``user``, ``assistant``, ``attachment``, ``queue-operation`` or others.
``message.content`` is a string or a list of blocks. Assistant ``tool_use`` blocks carry
``id``, ``name`` and ``input`` (``Bash``/``PowerShell``: ``command``; ``Edit``/``Write``/
``MultiEdit``: ``file_path``; ``NotebookEdit``: ``notebook_path``). Results come back in a
later user entry as ``tool_result`` blocks with ``tool_use_id``, ``content`` and ``is_error``;
a failed shell command's content starts with ``Exit code N``. ``isSidechain`` marks subagent
entries, which are not the main agent's own claims or runs.

The parser is tolerant: unknown entry types, malformed lines and missing fields are skipped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Final

from counterproof.probes.claim_audit.parsers.base import Command, FileEdit, Transcript

SHELL_TOOLS: Final = frozenset({"Bash", "PowerShell"})
EDIT_TOOLS: Final = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_EXIT: Final = re.compile(r"^(?:Error: )?Exit code (-?\d+)\b")
_KNOWN_TYPES: Final = frozenset({"user", "assistant"})


def _entries(lines: Sequence[str]) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append((number, entry))
    return out


def sniff(lines: Sequence[str]) -> bool:
    """A Claude Code transcript has user/assistant entries with a ``message`` object."""
    for _, entry in _entries(lines[:200]):
        if entry.get("type") in _KNOWN_TYPES and isinstance(entry.get("message"), dict):
            return True
    return False


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _exit_code(text: str, is_error: Any) -> int | None:
    match = _EXIT.match(text)
    if match:
        return int(match.group(1))
    if is_error is True:
        return None  # failed, code unknown (for example a timeout or a refused command)
    return 0


def parse(lines: Sequence[str]) -> Transcript:
    commands: dict[str, Command] = {}
    order: list[str] = []
    edits: dict[str, FileEdit] = {}
    failed_edits: set[str] = set()
    models: list[str] = []
    trailing: list[str] = []
    trailing_line: int | None = None
    index = 0

    for number, entry in _entries(lines):
        kind = entry.get("type")
        message = entry.get("message")
        if kind not in _KNOWN_TYPES or entry.get("isSidechain") or not isinstance(message, dict):
            continue
        timestamp = entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]

        if kind == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model and not model.startswith("<"):
                if model in models:
                    models.remove(model)
                models.append(model)
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    if not trailing:
                        trailing_line = number
                    trailing.append(block["text"])
                elif block.get("type") == "tool_use":
                    index += 1
                    trailing, trailing_line = [], None
                    _tool_use(
                        block,
                        index=index,
                        line=number,
                        timestamp=timestamp,
                        commands=commands,
                        order=order,
                        edits=edits,
                    )
            continue

        # user entry: tool results, or a new prompt (which ends any earlier final message)
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                use_id = block.get("tool_use_id")
                text = _result_text(block.get("content"))
                if use_id in commands:
                    cmd = commands[use_id]
                    commands[use_id] = Command(
                        index=cmd.index,
                        line=cmd.line,
                        command=cmd.command,
                        tool=cmd.tool,
                        timestamp=cmd.timestamp,
                        output=text,
                        exit_code=_exit_code(text, block.get("is_error")),
                    )
                elif use_id in edits and block.get("is_error") is True:
                    failed_edits.add(use_id)
            elif block.get("type") == "text" and str(block.get("text") or "").strip():
                index += 1
                trailing, trailing_line = [], None

    final = "\n".join(t for t in trailing if t.strip()).strip() or None
    return Transcript(
        format="claude_code",
        commands=[commands[k] for k in order],
        edits=sorted((e for k, e in edits.items() if k not in failed_edits), key=lambda e: e.index),
        final_message=final,
        final_message_line=trailing_line if final else None,
        author_models=models,
    )


def _tool_use(
    block: dict[str, Any],
    *,
    index: int,
    line: int,
    timestamp: str | None,
    commands: dict[str, Command],
    order: list[str],
    edits: dict[str, FileEdit],
) -> None:
    name = block.get("name")
    use_id = block.get("id")
    payload = block.get("input")
    if not isinstance(name, str) or not isinstance(use_id, str) or not isinstance(payload, dict):
        return
    if name in SHELL_TOOLS and isinstance(payload.get("command"), str):
        commands[use_id] = Command(
            index=index, line=line, command=payload["command"], tool=name, timestamp=timestamp
        )
        order.append(use_id)
    elif name in EDIT_TOOLS:
        path = payload.get("file_path") or payload.get("notebook_path")
        if isinstance(path, str):
            edits[use_id] = FileEdit(
                index=index, line=line, path=path, tool=name, timestamp=timestamp
            )
