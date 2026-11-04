"""``counterproof doctor``: environment diagnostics (PRD sections 11 and 21)."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from counterproof import git
from counterproof.errors import UsageError
from counterproof.evidence.store import cache_root, runs_root

Status = Literal["ok", "info", "warn", "fail"]
Which = Callable[[str], str | None]

MIN_PYTHON: Final = (3, 11)
MIN_GIT: Final = (2, 25, 0)


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def _version_of(exe: str) -> str:
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else ""


def _check_python() -> Check:
    found = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info[:2] >= MIN_PYTHON:
        return Check("python", "ok", f"Python {found}")
    return Check("python", "fail", f"Python {found}; need >= 3.11")


def _check_git(which: Which, version_of: Callable[[str], str]) -> Check:
    exe = which("git")
    if exe is None:
        return Check("git", "fail", "git not found on PATH (required for worktrees)")
    text = version_of(exe)
    version = git.parse_version(text)
    if version is None:
        return Check("git", "fail", f"cannot determine git version from {text!r}")
    if version < MIN_GIT:
        need = ".".join(map(str, MIN_GIT))
        return Check("git", "fail", f"{text}; need >= {need}")
    return Check("git", "ok", text)


def _check_cache(cache: Path) -> Check:
    runs = runs_root(cache)
    probe = runs / f".doctor-{secrets.token_hex(4)}"
    try:
        runs.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return Check("run directory", "fail", f"{runs} is not writable: {exc}")
    return Check("run directory", "ok", str(runs))


def _check_repo(cwd: Path) -> Check:
    try:
        root = git.repo_root(cwd)
    except UsageError:
        return Check("git repository", "info", f"{cwd} is not a git repository")
    return Check("git repository", "ok", str(root))


def _optional_tool(name: str, exe: str, purpose: str, which: Which) -> Check:
    path = which(exe)
    if path is None:
        return Check(name, "warn", f"{exe} not found ({purpose})")
    return Check(name, "ok", path)


def _check_provider(name: str, env_var: str) -> Check:
    if os.environ.get(env_var):
        return Check(f"provider: {name}", "ok", f"{env_var} is set")
    return Check(f"provider: {name}", "info", f"{env_var} not set (optional)")


def run_checks(
    *,
    cwd: Path | None = None,
    cache: Path | None = None,
    which: Which | None = None,
    version_of: Callable[[str], str] = _version_of,
) -> list[Check]:
    which = which or shutil.which
    cwd = cwd or Path.cwd()
    cache = cache if cache is not None else cache_root()
    ollama = which("ollama")
    return [
        _check_python(),
        _check_git(which, version_of),
        _check_cache(cache),
        _check_repo(cwd),
        _optional_tool("pytest", "pytest", "Python test runner on PATH", which),
        _optional_tool("node", "node", "needed for Vitest and Jest projects", which),
        _optional_tool("docker", "docker", "optional, for --sandbox docker", which),
        _check_provider("anthropic", "ANTHROPIC_API_KEY"),
        _check_provider("openai", "OPENAI_API_KEY"),
        Check(
            "provider: ollama",
            "ok" if ollama else "info",
            ollama or "ollama not found (optional local models)",
        ),
        Check("provider: none", "ok", "rule-based mutants only; always available"),
    ]


def passed(checks: list[Check]) -> bool:
    return all(c.status != "fail" for c in checks)
