"""Installers for the Claude Code Stop hook and the git pre-push hook (PRD 13.1, 13.4).

git calls ``pre-push <remote> <url>`` with one line per pushed ref on stdin:
``<local ref> <local sha> <remote ref> <remote sha>`` (an all-zero sha means a new remote
branch or a deleted local ref). The installed script hands that to
``counterproof hook pre-push``, which verifies each pushed commit range with the fast
profile and blocks only on REFUTED unless told otherwise.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from counterproof import git
from counterproof.errors import UsageError

HOOK_MARKER: Final = "counterproof hook"
STOP_HOOK_TIMEOUT_S: Final = 120


@dataclass(frozen=True)
class PushedRef:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def deleted(self) -> bool:
        return set(self.local_sha) == {"0"}

    @property
    def new_branch(self) -> bool:
        return set(self.remote_sha) == {"0"}


def parse_push_lines(text: str) -> list[PushedRef]:
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append(PushedRef(*parts))
    return refs


def counterproof_command() -> str:
    """``counterproof`` when it is on PATH, otherwise this interpreter with ``-m``."""
    if shutil.which("counterproof"):
        return "counterproof"
    return f'"{Path(sys.executable).as_posix()}" -m counterproof'


def pre_push_script(command: str, fail_on: str) -> str:
    return (
        "#!/bin/sh\n"
        "# Installed by `counterproof init --git-hook` (PRD 13.4). Blocks the push only when\n"
        f"# the pushed change is {fail_on.upper()} or worse.\n"
        f'exec {command} hook pre-push --fail-on {fail_on} "$@"\n'
    )


def hooks_dir(repo: Path) -> Path:
    """Where git looks for hooks: the user's ``core.hooksPath`` or ``<common dir>/hooks``.

    Read with a plain ``git config`` call: Counterproof's own git wrapper pins
    ``core.hooksPath`` to the null device so that its worktree operations never run hooks.
    """
    proc = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    configured = proc.stdout.strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else repo / path
    common = Path(git.run_git(["rev-parse", "--git-common-dir"], repo).strip())
    return (common if common.is_absolute() else repo / common) / "hooks"


def install_pre_push(repo: Path, *, fail_on: str = "refuted", force: bool = False) -> Path:
    hooks = hooks_dir(repo)
    target = hooks / "pre-push"
    if target.exists() and not force:
        existing = target.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER not in existing:
            raise UsageError(f"{target} already exists; use --force to replace it")
    hooks.mkdir(parents=True, exist_ok=True)
    target.write_text(
        pre_push_script(counterproof_command(), fail_on), encoding="utf-8", newline="\n"
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def install_claude_hook(repo: Path) -> tuple[Path, bool]:
    """Add the Stop hook to ``.claude/settings.json``; ``(path, changed)``. Idempotent."""
    path = repo / ".claude" / "settings.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise UsageError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise UsageError(f"{path} must contain a JSON object")
        data = loaded
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise UsageError(f"{path}: 'hooks' must be an object")
    stop = hooks.setdefault("Stop", [])
    if not isinstance(stop, list):
        raise UsageError(f"{path}: 'hooks.Stop' must be a list")
    for group in stop:
        for handler in group.get("hooks", []) if isinstance(group, dict) else []:
            if isinstance(handler, dict) and "hook claude-stop" in str(handler.get("command", "")):
                return path, False
    stop.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{counterproof_command()} hook claude-stop",
                    "timeout": STOP_HOOK_TIMEOUT_S,
                }
            ]
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, True
