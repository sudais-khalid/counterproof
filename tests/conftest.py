"""Shared fixtures. Every test gets an isolated cache dir so nothing touches the real one."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from counterproof.evidence.store import CACHE_DIR_ENV


@pytest.fixture(autouse=True)
def isolated_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    cache = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv(CACHE_DIR_ENV, str(cache))
    return cache


def _git_env(tmp: Path) -> dict[str, str]:
    """Isolate test git calls from the developer's global and system configuration."""
    empty = tmp / "empty.gitconfig"
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
    env = _git_env(repo.parent)
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A tiny committed repository with one uncommitted source edit (a WORKTREE head)."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "money.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_money.py").write_text(
        "from money import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    (repo / "src" / "money.py").write_text(
        "def add(a: int, b: int) -> int:\n    return int(a) + int(b)\n", encoding="utf-8"
    )
    (repo / "notes.txt").write_text("untracked file\n", encoding="utf-8")
    return repo


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


@pytest.fixture
def in_repo(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(git_repo)
    yield git_repo
