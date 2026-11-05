"""Validates the C1 fixture corpus itself, independently of Counterproof's own code.

If these fail, the fixtures (not the tool) are wrong: every variant must be green on its
head, and its declared ``red_on_base`` tests must fail when run against the base source.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import FIXTURE_REPOS, all_variants, build_repo, load_expectations

pytestmark = pytest.mark.integration

LEDGER = FIXTURE_REPOS / "py_ledger"
TASKS = load_expectations()["repos"]["py_ledger"]["tasks"]


def _pytest(repo: Path, *args: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=str(repo / "src"), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE", "--tb=no", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, proc.stdout


def _failed_ids(output: str) -> set[str]:
    return set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, flags=re.MULTILINE))


def test_base_suite_is_green(tmp_path: Path) -> None:
    built = build_repo(tmp_path / "repo")
    code, out = _pytest(built.path)
    assert code == 0, out


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_variant_head_is_green(tmp_path: Path, task: str, variant: str) -> None:
    built = build_repo(tmp_path / "repo", task=task, variant=variant)
    code, out = _pytest(built.path)
    assert code == 0, out


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_variant_red_on_base_tests_fail_on_base(tmp_path: Path, task: str, variant: str) -> None:
    """Transplant the variant's test files onto base and run the declared red tests."""
    red = TASKS[task]["variants"][variant]["red_on_base"]
    if not red:
        pytest.skip("variant declares no red-on-base tests")
    built = build_repo(tmp_path / "repo")
    overlay = LEDGER / "tasks" / task / variant
    for test_file in (overlay / "tests").rglob("*.py"):
        shutil.copy(test_file, built.path / "tests" / test_file.name)
    code, out = _pytest(built.path, *red)
    assert code == 1, out
    assert _failed_ids(out) == set(red), out


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_variant_touches_only_declared_files(task: str, variant: str) -> None:
    overlay = LEDGER / "tasks" / task / variant
    touched = sorted(p.relative_to(overlay).as_posix() for p in overlay.rglob("*") if p.is_file())
    assert touched == sorted(TASKS[task]["files"])
    for rel in touched:
        assert (LEDGER / "base" / rel).is_file(), f"{rel} must modify an existing base file"
        assert (overlay / rel).read_bytes() != (LEDGER / "base" / rel).read_bytes()


def test_every_task_has_text_and_four_variants() -> None:
    for task in TASKS:
        task_dir = LEDGER / "tasks" / task
        assert (task_dir / "task.md").read_text(encoding="utf-8").strip()
        assert sorted(p.name for p in task_dir.iterdir() if p.is_dir()) == sorted(
            ["honest", "hardcoded", "weakened", "partial"]
        )
