"""The ts_cart fixture (corpus C1, TypeScript + Vitest) is built faithfully."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import FIXTURE_REPOS, VARIANTS, all_variants, build_repo, git

pytestmark = pytest.mark.integration

CART = FIXTURE_REPOS / "ts_cart"


@pytest.mark.parametrize(("task", "variant"), all_variants("ts_cart"))
def test_head_commit_contains_every_overlay_file(tmp_path: Path, task: str, variant: str) -> None:
    """Regression (M6): an overlay file with the same size as the base file and an mtime in
    the same second (``copytree`` keeps fixture mtimes) was skipped by git's stat cache, so
    the head commit silently kept the base source (T5 honest: ``floor`` -> ``round``)."""
    built = build_repo(tmp_path / "repo", "ts_cart", task, variant)
    overlay = CART / "tasks" / task / variant
    for path in overlay.rglob("*"):
        if path.is_file():
            rel = path.relative_to(overlay).as_posix()
            committed = git(built.path, "show", f"HEAD:{rel}")
            assert committed == path.read_text(encoding="utf-8"), rel


def test_every_task_has_text_and_four_variants() -> None:
    for task_dir in sorted((CART / "tasks").iterdir()):
        assert (task_dir / "task.md").read_text(encoding="utf-8").strip()
        assert sorted(p.name for p in task_dir.iterdir() if p.is_dir()) == sorted(VARIANTS)
