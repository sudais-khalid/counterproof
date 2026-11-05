"""ChangeResolver: base/head commits to a typed :class:`ChangeSet` (FR-CR-01 to FR-CR-07)."""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Final

from counterproof import git
from counterproof.change.classify import TestMatcher, classify
from counterproof.change.diff import parse_unified_diff
from counterproof.change.intent import infer_intent
from counterproof.change.self_config import SELF_CONFIG_FLAG, self_config_paths
from counterproof.change.task import TaskText
from counterproof.models import WORKTREE, ChangedFile, ChangeSet, Hunk, Intent

NO_CODE_CHANGE: Final = "NO_CODE_CHANGE"
DIFF_TIMEOUT_S: Final = 120.0


def diff_text(repo: Path, base_sha: str, head_sha: str) -> str:
    """FR-CR-02: zero-context diff between two commits (never the working tree)."""
    return git.run_git(
        [
            "diff",
            "-U0",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            base_sha,
            head_sha,
            "--",
        ],
        repo,
        timeout=DIFF_TIMEOUT_S,
    )


def commit_message_for(repo: Path, base_sha: str, head_ref: str) -> str | None:
    """FR-CR-04 (last resort): the message of the commit that carries the change."""
    rev = "HEAD" if head_ref == WORKTREE else head_ref
    sha = git.try_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], repo)
    if sha is None or sha.strip() == base_sha:
        return None  # uncommitted work on top of base: HEAD's message describes older work
    return git.try_git(["log", "-1", "--format=%B", sha.strip()], repo)


def resolve_change(
    repo: Path,
    *,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    task: TaskText,
    transcript: Path | None = None,
    user_intent: Intent | None = None,
    extra_test_matchers: Iterable[TestMatcher] = (),
) -> ChangeSet:
    matchers = list(extra_test_matchers)
    file_diffs = parse_unified_diff(diff_text(repo, base_sha, head_sha))
    files = [
        ChangedFile(
            path=fd.path,
            role=classify(fd.path, matchers),
            status=fd.status,
            old_path=fd.old_path,
            binary=fd.binary,
        )
        for fd in file_diffs
    ]
    hunks: list[Hunk] = [h for fd in file_diffs for h in fd.hunks]
    intent, intent_source = infer_intent(files, task.text, user_intent)

    flags: list[str] = []
    # FR-CR-07: nothing but binary files (or nothing at all) is not a code change.
    if not files or all(f.binary for f in files):
        flags.append(NO_CODE_CHANGE)
    if self_config_paths(
        files, partial(git.show_file, repo, base_sha), partial(git.show_file, repo, head_sha)
    ):
        flags.append(SELF_CONFIG_FLAG)

    return ChangeSet(
        repo_root=repo,
        base_sha=base_sha,
        head_ref=head_ref,
        head_snapshot_sha=head_sha,
        files=files,
        hunks=hunks,
        intent=intent,
        intent_source=intent_source,
        task_text=task.text,
        transcript_path=transcript.resolve() if transcript else None,
        flags=flags,
    )
