"""Git helpers used against the user's repository.

Nothing here may modify the user's working tree, index or stash. ``GIT_OPTIONAL_LOCKS=0``
stops commands such as ``git status`` from opportunistically refreshing the index, and
:data:`SAFE_CONFIG` disables hooks and fsmonitor for every call.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from counterproof.errors import GitError, UsageError

DEFAULT_TIMEOUT_S: Final = 30.0


# Inherited variables that would silently point our commands at another repo or index.
_REDIRECT_VARS: Final = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)

# Hooks and fsmonitor can run arbitrary programs from the repository; never let them.
SAFE_CONFIG: Final = (
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotePath=false",
    "-c",
    "advice.detachedHead=false",
)


def _git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in _REDIRECT_VARS}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update(extra)
    return env


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
) -> str:
    """Run ``git <args>`` in ``cwd`` and return stdout; raise :class:`GitError` on failure."""
    cmd = ["git", *SAFE_CONFIG, *args]
    shown = " ".join(["git", *args])
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_git_env(env),
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
        raise GitError(f"`{shown}` timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise GitError(f"`{shown}` failed ({proc.returncode}): {detail}")
    return proc.stdout


def try_git(args: list[str], cwd: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> str | None:
    """Like :func:`run_git` but returns ``None`` instead of raising."""
    try:
        return run_git(args, cwd, timeout=timeout)
    except GitError:
        return None


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


def show_file(repo: Path, rev: str, path: str) -> str | None:
    """Content of ``path`` at ``rev`` (UTF-8, replacement on errors), or ``None`` if absent."""
    return try_git(["show", f"{rev}:{path}"], repo)


def default_branch(repo: Path) -> str | None:
    """FR-CR-01: ``origin/HEAD`` if set, else the first of main/master that exists."""
    out = try_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repo)
    if out and out.strip():
        return out.strip().removeprefix("refs/remotes/")
    for name in ("main", "master", "origin/main", "origin/master"):
        if try_git(["rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"], repo):
            return name
    return None


def default_base(repo: Path) -> str:
    """FR-CR-01: merge-base with the default branch if HEAD is ahead of it, else ``HEAD``."""
    head = resolve_commit(repo, "HEAD")
    branch = default_branch(repo)
    if branch is None:
        return head
    ahead = try_git(["rev-list", "--count", f"{branch}..HEAD"], repo)
    if not ahead or int(ahead.strip() or "0") == 0:
        return head
    merge_base = try_git(["merge-base", "HEAD", branch], repo)
    return merge_base.strip() if merge_base and merge_base.strip() else head
