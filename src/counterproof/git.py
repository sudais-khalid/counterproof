"""Thin, read-only git helpers used against the user's repository.

Nothing here may modify the user's working tree, index or stash. ``GIT_OPTIONAL_LOCKS=0``
stops commands such as ``git status`` from opportunistically refreshing the index.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Final

from counterproof.errors import GitError, UsageError

DEFAULT_TIMEOUT_S: Final = 30.0


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_git(args: list[str], cwd: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """Run ``git <args>`` in ``cwd`` and return stdout; raise :class:`GitError` on failure."""
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"`{' '.join(cmd)}` timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise GitError(f"`{' '.join(cmd)}` failed ({proc.returncode}): {detail}")
    return proc.stdout


def repo_root(path: Path) -> Path:
    """Top level of the git repository containing ``path``."""
    try:
        out = run_git(["rev-parse", "--show-toplevel"], path)
    except GitError as exc:
        raise UsageError(f"{path} is not inside a git repository") from exc
    return Path(out.strip()).resolve()


def resolve_commit(repo: Path, ref: str) -> str:
    """FR-CR-01: resolve a user-supplied ref to a full commit sha."""
    if ref.startswith("-"):
        raise UsageError(f"invalid git ref {ref!r}")
    try:
        out = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo)
    except GitError as exc:
        raise UsageError(f"cannot resolve {ref!r} to a commit in {repo}") from exc
    return out.strip()


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Parse ``git version 2.54.0.windows.1`` into ``(2, 54, 0)``."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
