"""Test helpers: isolated git, working-tree hashing and the fixture repo builder."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

TESTS_DIR: Final = Path(__file__).resolve().parent
FIXTURE_REPOS: Final = TESTS_DIR / "fixtures" / "repos"
EXPECTATIONS: Final = TESTS_DIR / "golden" / "expectations.yaml"
VARIANTS: Final = ("honest", "hardcoded", "weakened", "partial")


def git_env(scratch: Path) -> dict[str, str]:
    """Isolate test git calls from the developer's global and system configuration."""
    empty = scratch / "empty.gitconfig"
    empty.touch()
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(empty),
        GIT_AUTHOR_NAME="Fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    )
    return env


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=git_env(repo.parent),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def tree_hash(root: Path) -> str:
    """Hash every path and file byte under ``root``, excluding ``.git``."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        digest.update(rel.as_posix().encode() + b"\0")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def repo_state(repo: Path) -> dict[str, str]:
    """Everything a run must leave untouched: tree bytes, index, refs, stash, status."""
    index = repo / ".git" / "index"
    return {
        "tree": tree_hash(repo),
        "index": hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else "",
        "refs": git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        "stash": git(repo, "stash", "list"),
        "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        "head": git(repo, "rev-parse", "HEAD"),
    }


def load_expectations() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(EXPECTATIONS.read_text(encoding="utf-8"))
    return data


@dataclass(frozen=True)
class BuiltRepo:
    path: Path
    base_sha: str
    head_sha: str | None
    task_text: str | None


def build_repo(
    dest: Path,
    name: str = "py_ledger",
    task: str | None = None,
    variant: str | None = None,
    *,
    commit_head: bool = True,
) -> BuiltRepo:
    """Materialise a fixture as a real git repository.

    The base tree is committed on ``main``. With a task and variant, the overlay is copied
    on top on branch ``agent/<task>`` and either committed (message = task text) or left
    uncommitted as a WORKTREE head.
    """
    source = FIXTURE_REPOS / name
    shutil.copytree(source / "base", dest)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", f"Initial {name} library")
    base_sha = git(dest, "rev-parse", "HEAD").strip()
    if task is None or variant is None:
        return BuiltRepo(dest, base_sha, None, None)
    task_dir = source / "tasks" / task
    task_text = (task_dir / "task.md").read_text(encoding="utf-8").strip()
    git(dest, "checkout", "-q", "-b", f"agent/{task}")
    shutil.copytree(task_dir / variant, dest, dirs_exist_ok=True)
    head_sha = None
    if commit_head:
        git(dest, "add", "-A")
        git(dest, "commit", "-q", "-m", task_text)
        head_sha = git(dest, "rev-parse", "HEAD").strip()
    return BuiltRepo(dest, base_sha, head_sha, task_text)


def all_variants() -> list[tuple[str, str]]:
    expectations = load_expectations()["repos"]["py_ledger"]["tasks"]
    return [(task, variant) for task in sorted(expectations) for variant in VARIANTS]
