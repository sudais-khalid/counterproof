"""Shared fixtures. Every test gets an isolated cache dir so nothing touches the real one."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from counterproof.adapters.pytest_adapter import INTERPRETER_ENV
from counterproof.evidence.store import CACHE_DIR_ENV
from tests.helpers import git


@pytest.fixture(autouse=True)
def isolated_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    cache = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv(CACHE_DIR_ENV, str(cache))
    # Fixture repos run under this interpreter, which has pytest and pytest-cov installed;
    # without this the developer's $VIRTUAL_ENV would decide.
    monkeypatch.setenv(INTERPRETER_ENV, sys.executable)
    return cache


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


@pytest.fixture
def in_repo(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(git_repo)
    yield git_repo
