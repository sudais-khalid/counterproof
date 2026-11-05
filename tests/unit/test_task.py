"""FR-CR-04: task text priority and tolerant transcript parsing."""

from __future__ import annotations

import json
from pathlib import Path

from counterproof.change.task import first_user_message, pull_request_text, resolve_task_text


def _jsonl(path: Path, entries: list[object]) -> Path:
    path.write_text(
        "\n".join(e if isinstance(e, str) else json.dumps(e) for e in entries), encoding="utf-8"
    )
    return path


def test_transcript_first_user_text_skips_noise(tmp_path: Path) -> None:
    transcript = _jsonl(
        tmp_path / "s.jsonl",
        [
            {"type": "queue-operation", "operation": "enqueue"},
            "not json at all",
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "sub"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "ignored"}],
                },
            },
            {
                "type": "user",
                "extra_field_from_a_newer_version": 1,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Fix the discount cap."},
                        {"type": "image", "source": {}},
                        {"type": "text", "text": "It returns 50."},
                    ],
                },
            },
            {"type": "user", "message": {"role": "user", "content": "second message"}},
        ],
    )
    assert first_user_message(transcript) == "Fix the discount cap.\nIt returns 50."


def test_transcript_string_content_and_missing_file(tmp_path: Path) -> None:
    transcript = _jsonl(
        tmp_path / "s.jsonl", [{"type": "user", "message": {"role": "user", "content": "  hi  "}}]
    )
    assert first_user_message(transcript) == "hi"
    assert first_user_message(tmp_path / "missing.jsonl") is None


def test_pull_request_text_from_github_event(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"title": "Cap discounts", "body": "Closes #4"}}))
    assert pull_request_text({"GITHUB_EVENT_PATH": str(event)}) == "Cap discounts\n\nCloses #4"
    event.write_text(json.dumps({"push": {}}))
    assert pull_request_text({"GITHUB_EVENT_PATH": str(event)}) is None
    assert pull_request_text({}) is None


def test_fr_cr_04_priority_order(tmp_path: Path) -> None:
    transcript = _jsonl(
        tmp_path / "s.jsonl", [{"type": "user", "message": {"role": "user", "content": "from t"}}]
    )
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"title": "from pr", "body": None}}))
    env = {"GITHUB_EVENT_PATH": str(event)}

    def resolve(**kw: object) -> tuple[str | None, str]:
        args: dict[str, object] = {
            "cli_text": None,
            "file_text": None,
            "transcript": None,
            "commit_message": None,
            "env": env,
        }
        args.update(kw)
        task = resolve_task_text(**args)  # type: ignore[arg-type]
        return task.text, task.source

    assert resolve(cli_text="cli", file_text="file", transcript=transcript) == ("cli", "cli")
    assert resolve(file_text="file", transcript=transcript) == ("file", "file")
    assert resolve(transcript=transcript, commit_message="c") == ("from t", "transcript")
    assert resolve(commit_message="c") == ("from pr", "pull_request")
    assert resolve(commit_message="commit msg", env={}) == ("commit msg", "commit")
    assert resolve(cli_text="   ", env={}) == (None, "none")
