"""FR-CR-01 default base, FR-WS-01 snapshot and worktrees, FR-WS-07 cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

from counterproof import git as cp_git
from counterproof.change.resolver import resolve_change
from counterproof.change.task import TaskText
from counterproof.workspace.manager import WorkspaceManager
from tests.helpers import all_variants, build_repo, git, repo_state

pytestmark = pytest.mark.integration


def test_fr_cr_01_default_base_is_merge_base_when_branch_is_ahead(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="honest")
    assert cp_git.default_base(built.path) == built.base_sha


def test_fr_cr_01_default_base_is_head_on_default_branch(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo")
    (built.path / "src" / "ledger" / "tax.py").write_text("# edited\n", encoding="utf-8")
    assert cp_git.default_base(built.path) == built.base_sha  # HEAD itself


def test_fr_cr_01_default_base_after_main_moves_on(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo", task="T2-round-half-up", variant="honest")
    git(built.path, "checkout", "-q", "main")
    (built.path / "README.md").write_text("main moved on\n", encoding="utf-8")
    git(built.path, "commit", "-qam", "docs on main")
    git(built.path, "checkout", "-q", "agent/T2-round-half-up")
    assert cp_git.default_base(built.path) == built.base_sha


def _apply_hunks(base_text: str, hunks: list) -> str:  # type: ignore[type-arg]
    """Rebuild the head file from the base file and -U0 hunks (independent oracle)."""
    lines = base_text.splitlines()
    out: list[str] = []
    cursor = 0  # number of base lines consumed
    for hunk in hunks:
        removed = [line[1:] for line in hunk.lines if line.startswith("-")]
        added = [line[1:] for line in hunk.lines if line.startswith("+")]
        start = hunk.old_start - 1 if hunk.old_len else hunk.old_start
        out.extend(lines[cursor:start])
        assert lines[start : start + hunk.old_len] == removed
        out.extend(added)
        cursor = start + hunk.old_len
    out.extend(lines[cursor:])
    return "\n".join(out) + "\n"


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_fr_cr_02_hunks_rebuild_every_variant_exactly(
    tmp_path: Path, task: str, variant: str
) -> None:
    built = build_repo(tmp_path / "repo", task=task, variant=variant)
    assert built.head_sha is not None
    change = resolve_change(
        built.path,
        base_sha=built.base_sha,
        head_ref=built.head_sha,
        head_sha=built.head_sha,
        task=TaskText(built.task_text, "commit"),
    )
    assert {f.path: f.role for f in change.files} == {
        p: ("test" if p.startswith("tests/") else "source") for p in {f.path for f in change.files}
    }
    for changed in change.files:
        base_text = git(built.path, "show", f"{built.base_sha}:{changed.path}")
        head_text = (built.path / changed.path).read_text(encoding="utf-8")
        hunks = [h for h in change.hunks if h.path == changed.path]
        assert _apply_hunks(base_text, hunks) == head_text
    assert change.intent == "bugfix"
    assert change.flags == []


def test_fr_cr_02_t1_honest_hunks_by_construction(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="honest")
    assert built.head_sha
    change = resolve_change(
        built.path,
        base_sha=built.base_sha,
        head_ref=built.head_sha,
        head_sha=built.head_sha,
        task=TaskText(None, "none"),
    )
    source = [h for h in change.hunks if h.path == "src/ledger/discounts.py"]
    assert [(h.old_start, h.old_len, h.new_start, h.new_len) for h in source] == [(17, 0, 18, 2)]
    assert source[0].lines == ["+    if cap is not None and discount > cap:", "+        return cap"]
    tests = [h for h in change.hunks if h.path == "tests/test_discounts.py"]
    assert len(tests) == 1
    assert tests[0].old_len == 0
    assert sum(1 for line in tests[0].lines if line.startswith("+def test_")) == 3


def test_fr_ws_01_snapshot_includes_untracked_and_leaves_index_alone(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo")
    repo = built.path
    (repo / "src" / "ledger" / "tax.py").write_text("# modified\n", encoding="utf-8")
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    pass\n", encoding="utf-8")
    (repo / "src" / "ledger" / "__pycache__").mkdir()
    (repo / "src" / "ledger" / "__pycache__" / "x.pyc").write_bytes(b"ignored")
    git(repo, "add", "src/ledger/tax.py")  # a staged change must stay staged
    before = repo_state(repo)

    with WorkspaceManager(repo, tmp_path / "run" / "ws") as wm:
        sha = wm.snapshot_worktree()
        again = wm.snapshot_worktree()
        files = git(repo, "ls-tree", "-r", "--name-only", sha).split()
    assert sha == again, "same tree and parent must give the same snapshot sha"
    assert "tests/test_new.py" in files
    assert not any("__pycache__" in f for f in files)
    assert git(repo, "show", f"{sha}:src/ledger/tax.py") == "# modified\n"
    assert repo_state(repo) == before


def test_fr_ws_01_worktrees_created_and_removed(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="honest")
    assert built.head_sha
    before = repo_state(built.path)
    root = tmp_path / "run" / "ws"
    with WorkspaceManager(built.path, root) as wm:
        head = wm.add("head", built.head_sha)
        base = wm.add("base", built.base_sha)
        assert (head.root / "tests" / "test_discounts.py").read_text(encoding="utf-8").count(
            "def test_"
        ) == 7
        assert (base.root / "tests" / "test_discounts.py").read_text(encoding="utf-8").count(
            "def test_"
        ) == 4
        assert len(git(built.path, "worktree", "list", "--porcelain").split("worktree ")) == 4
    assert not (root / "head").exists()
    assert not (root / "base").exists()
    assert git(built.path, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert repo_state(built.path) == before


def test_fr_ws_07_cleanup_runs_on_interrupt(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo")
    root = tmp_path / "run" / "ws"
    with pytest.raises(KeyboardInterrupt), WorkspaceManager(built.path, root) as wm:
        wm.add("head", built.base_sha)
        raise KeyboardInterrupt
    assert not (root / "head").exists()
    assert git(built.path, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_keep_leaves_worktrees_in_place(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo")
    root = tmp_path / "run" / "ws"
    with WorkspaceManager(built.path, root, keep=True) as wm:
        wm.add("head", built.base_sha)
    assert (root / "head" / "pyproject.toml").is_file()
    WorkspaceManager(built.path, root).cleanup()  # no-op: this instance tracks nothing


def test_git_hooks_never_run(tmp_path: Path) -> None:
    """A malicious post-checkout hook in the user's repo must not run during a worktree add."""
    built = build_repo(tmp_path / "repo")
    marker = tmp_path / "pwned"
    hook = built.path / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho hacked > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    # Control: the hook does fire for a plain git worktree add.
    git(built.path, "worktree", "add", "--detach", str(tmp_path / "control"), built.base_sha)
    assert marker.exists(), "control failed: hook did not run under plain git"
    marker.unlink()
    git(built.path, "worktree", "remove", "--force", str(tmp_path / "control"))

    with WorkspaceManager(built.path, tmp_path / "run" / "ws") as wm:
        wm.add("head", built.base_sha)
    assert not marker.exists()
