"""Isolated git worktrees for base and head (FR-WS-01, FR-WS-07, PRD section 6.3).

The user's working tree, index and stash are never written. A WORKTREE head is snapshotted
by staging into a *temporary* index file (``GIT_INDEX_FILE``) and turning that into a
dangling commit object; only new objects and worktree metadata are added to ``.git``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final

from counterproof import git
from counterproof.errors import GitError
from counterproof.evidence.store import remove_tree
from counterproof.log import get_logger

log = get_logger("workspace")

# Fixed identity and dates make the snapshot sha a pure function of tree and parent.
SNAPSHOT_IDENTITY: Final = {
    "GIT_AUTHOR_NAME": "counterproof",
    "GIT_AUTHOR_EMAIL": "snapshot@counterproof.invalid",
    "GIT_COMMITTER_NAME": "counterproof",
    "GIT_COMMITTER_EMAIL": "snapshot@counterproof.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}
WORKTREE_TIMEOUT_S: Final = 300.0


@dataclass(frozen=True)
class Workspace:
    name: str
    root: Path
    sha: str


class WorkspaceManager:
    """Creates and always removes (unless ``keep``) worktrees under ``root``."""

    def __init__(self, repo: Path, root: Path, *, keep: bool = False) -> None:
        self.repo = repo
        self.root = root
        self.keep = keep
        self._workspaces: dict[str, Workspace] = {}

    def __enter__(self) -> WorkspaceManager:
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # FR-WS-07: runs on normal exit, on exceptions and on KeyboardInterrupt.
        if not self.keep:
            self.cleanup()

    @property
    def workspaces(self) -> dict[str, Workspace]:
        return dict(self._workspaces)

    def snapshot_worktree(self) -> str:
        """Commit object for the current working tree (tracked + untracked, not ignored)."""
        self.root.mkdir(parents=True, exist_ok=True)
        index = self.root / "snapshot.index"
        real_index = Path(git.run_git(["rev-parse", "--git-path", "index"], self.repo).strip())
        if not real_index.is_absolute():
            real_index = self.repo / real_index
        env = {"GIT_INDEX_FILE": str(index)}
        if real_index.is_file():
            # A copy keeps the stat cache, so `add -A` only rehashes changed files. copy2 also
            # keeps the index timestamp: git re-reads "racily clean" entries (files written in
            # the same second as the index) only when the index looks as old as it really is.
            shutil.copy2(real_index, index)
        else:
            git.run_git(["read-tree", "HEAD"], self.repo, env=env)
        # No split-index or untracked-cache side files: only objects are added to .git.
        git.run_git(
            [
                "-c",
                "core.splitIndex=false",
                "-c",
                "core.untrackedCache=false",
                "add",
                "-A",
                "--",
                ":/",
            ],
            self.repo,
            env=env,
            timeout=WORKTREE_TIMEOUT_S,
        )
        tree = git.run_git(["write-tree"], self.repo, env=env).strip()
        sha = git.run_git(
            ["commit-tree", tree, "-p", "HEAD", "-m", "counterproof: working tree snapshot"],
            self.repo,
            env=SNAPSHOT_IDENTITY,
        ).strip()
        index.unlink(missing_ok=True)
        log.info("snapshot of working tree: %s (tree %s)", sha, tree)
        return sha

    def add(self, name: str, sha: str) -> Workspace:
        """FR-WS-01: ``git worktree add --detach <dir> <sha>`` under the run directory."""
        if name in self._workspaces:
            raise GitError(f"workspace {name!r} already exists")
        target = self.root / name
        git.run_git(
            ["worktree", "add", "--detach", str(target), sha],
            self.repo,
            timeout=WORKTREE_TIMEOUT_S,
        )
        ws = Workspace(name=name, root=target.resolve(), sha=sha)
        self._workspaces[name] = ws
        log.info("worktree %s at %s -> %s", name, sha[:12], target)
        return ws

    def cleanup(self) -> None:
        for name, ws in list(self._workspaces.items()):
            try:
                git.run_git(["worktree", "remove", "--force", str(ws.root)], self.repo)
            except GitError as exc:
                log.warning("worktree remove failed for %s: %s", ws.root, exc)
                if ws.root.exists():
                    remove_tree(ws.root)
            del self._workspaces[name]
        git.try_git(["worktree", "prune"], self.repo)
