"""M7 units: Stop-hook protocol (PRD 13.1), installers (13.1, 13.4), the GitHub Action
definition (13.3). No Counterproof run here; see tests/integration/test_m7.py for those."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from counterproof.errors import UsageError
from counterproof.integrations import claude_hook
from counterproof.integrations.git_hook import (
    install_claude_hook,
    install_pre_push,
    parse_push_lines,
)
from counterproof.workspace.sandbox_none import ProcessResult
from tests.helpers import TESTS_DIR, build_repo

ACTION = TESTS_DIR.parent / "action" / "action.yml"


def test_fr_13_1_hook_input_is_parsed_tolerantly(tmp_path: Path) -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "~/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "All tests pass.",
            "background_tasks": [],
            "something_new": {"x": 1},
        }
    )
    hook = claude_hook.parse_input(raw, default_cwd=Path("/fallback"))
    assert hook.cwd == tmp_path and hook.session_id == "abc"
    assert hook.transcript_path == Path("~/t.jsonl").expanduser()
    assert hook.last_assistant_message == "All tests pass." and not hook.stop_hook_active
    for junk in ("", "not json", "[1, 2]", json.dumps({"cwd": 5, "stop_hook_active": "yes"})):
        parsed = claude_hook.parse_input(junk, default_cwd=Path("/fallback"))
        assert parsed.cwd == Path("/fallback") and not parsed.stop_hook_active


def _never_called(*_: Any) -> ProcessResult:
    raise AssertionError("verify must not run")


def test_fr_13_1_active_stop_hook_never_runs_again(tmp_path: Path) -> None:
    raw = json.dumps({"cwd": str(tmp_path), "stop_hook_active": True})
    assert claude_hook.run_hook(raw, default_cwd=tmp_path, runner=_never_called) is None


def test_fr_13_1_outside_a_repository_the_hook_stays_silent(tmp_path: Path) -> None:
    raw = json.dumps({"cwd": str(tmp_path)})
    assert claude_hook.run_hook(raw, default_cwd=tmp_path, runner=_never_called) is None


def _result(stdout: str = "", *, timed_out: bool = False, stderr: str = "") -> ProcessResult:
    return ProcessResult(["x"], 0, stdout, stderr, 1.0, timed_out, False)


def test_fr_13_1_timeouts_and_failures_never_block(tmp_path: Path) -> None:
    repo = build_repo(tmp_path / "repo").path
    raw = json.dumps({"cwd": str(repo), "last_assistant_message": "done"})
    seen: list[list[str]] = []

    def slow(cmd: list[str], cwd: Path, timeout_s: float) -> ProcessResult:
        seen.append(cmd)
        assert timeout_s == claude_hook.HOOK_TIMEOUT_S and cwd == repo
        message = Path(cmd[cmd.index("--final-message-file") + 1])
        assert message.read_text(encoding="utf-8") == "done"
        return _result(timed_out=True)

    out = claude_hook.run_hook(raw, default_cwd=tmp_path, runner=slow, cache=tmp_path / "c")
    assert out == {"systemMessage": "Counterproof: no verdict within 90s (not blocking)."}
    assert "--profile" in seen[0] and "fast" in seen[0] and "coach" in seen[0]
    assert not Path(seen[0][seen[0].index("--final-message-file") + 1]).exists()

    def broken(*_: Any) -> ProcessResult:
        return _result("Traceback ...", stderr="RuntimeError: boom")

    out = claude_hook.run_hook(raw, default_cwd=tmp_path, runner=broken, cache=tmp_path / "c")
    assert out is not None and "not blocking" in out["systemMessage"] and "decision" not in out


def test_hook_main_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: Any, **__: Any) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(claude_hook, "run_hook", explode)
    out = json.loads(claude_hook.main("{}", env={"CLAUDE_PROJECT_DIR": str(tmp_path)}))
    assert out == {"systemMessage": "Counterproof hook error (RuntimeError); not blocking."}


def test_fr_13_1_init_claude_merges_settings_idempotently(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    existing = {
        "permissions": {"allow": ["Bash(ls)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
    }
    settings.write_text(json.dumps(existing), encoding="utf-8")
    path, changed = install_claude_hook(tmp_path)
    assert changed and path == settings
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"] == existing["permissions"]
    commands = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert commands[0] == "say done" and commands[1].endswith("hook claude-stop")
    assert data["hooks"]["Stop"][1]["hooks"][0]["timeout"] == 120
    assert install_claude_hook(tmp_path) == (settings, False)
    settings.write_text("{not json", encoding="utf-8")
    with pytest.raises(UsageError, match="not valid JSON"):
        install_claude_hook(tmp_path)


def test_fr_13_4_pre_push_hook_install_and_stdin(tmp_path: Path) -> None:
    repo = build_repo(tmp_path / "repo").path
    hook = install_pre_push(repo, fail_on="unproven")
    text = hook.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n") and 'hook pre-push --fail-on unproven "$@"' in text
    if os.name != "nt":  # Windows has no execute bit; Git for Windows runs hooks anyway
        assert hook.stat().st_mode & stat.S_IXUSR
    assert install_pre_push(repo) == hook  # ours: replaced without --force
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with pytest.raises(UsageError, match="already exists"):
        install_pre_push(repo)
    assert "hook pre-push" in install_pre_push(repo, force=True).read_text(encoding="utf-8")

    zero = "0" * 40
    refs = parse_push_lines(
        f"refs/heads/a {'1' * 40} refs/heads/a {'2' * 40}\n"
        f"refs/heads/new {'3' * 40} refs/heads/new {zero}\n"
        f"(delete) {zero} refs/heads/old {'4' * 40}\nmalformed line\n"
    )
    assert [(r.local_ref, r.new_branch, r.deleted) for r in refs] == [
        ("refs/heads/a", False, False),
        ("refs/heads/new", True, False),
        ("(delete)", False, True),
    ]


def _action() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    return data


def test_fr_13_3_action_definition() -> None:
    action = _action()
    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["fail-on"]["default"] == "refuted"
    assert action["inputs"]["provider"]["default"] == "none"
    assert set(action["outputs"]) == {"verdict", "run-id"}
    steps = action["runs"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses == ["github/codeql-action/upload-sarif@v4"]
    names = [s["name"] for s in steps]
    assert names[-1] == "Gate on the verdict"  # comment and SARIF happen before failing


def test_fr_13_3_action_never_interpolates_untrusted_text_into_scripts() -> None:
    """Script injection: PR text reaches shell scripts only through environment variables."""
    for step in _action()["runs"]["steps"]:
        script = step.get("run", "")
        assert "${{" not in script, step["name"]
    verify = next(s for s in _action()["runs"]["steps"] if s.get("id") == "verify")
    assert verify["env"]["PR_BODY"] == "${{ github.event.pull_request.body }}"
    assert "provider=none" in verify["run"] and "IS_FORK" in verify["run"]
