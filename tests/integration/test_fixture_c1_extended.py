"""Ground truth of the extended C1 tier (tasks T6 to T20 of py_ledger and ts_cart), checked
with the real test runners and no Counterproof code. If these fail, the fixtures are wrong:

* every variant is green on its head, and its ``red_on_base`` tests fail on the base source;
* the hardcoded and partial variants are really wrong: the honest tests fail on their source;
* the weakened variant really hid a regression: the base tests fail on its source;
* the fixture files match their source data (tests/golden/make_c1_tasks.py --check).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from counterproof.workspace.links import link_dir, unlink_all
from tests.helpers import (
    FIXTURE_NODE_MODULES,
    FIXTURE_REPOS,
    TESTS_DIR,
    load_expectations,
    skip_without_node,
    task_order,
)

pytestmark = pytest.mark.integration
EXPECTATIONS: dict[str, Any] = load_expectations()["repos"]


def _extended(repo: str) -> list[Any]:
    tasks = EXPECTATIONS[repo]["tasks"]
    marks = [pytest.mark.corpus] + ([skip_without_node()] if repo == "ts_cart" else [])
    return [
        pytest.param(repo, task, marks=marks, id=f"{repo}-{task}")
        for task in sorted(tasks, key=task_order)
        if tasks[task].get("tier") == "extended"
    ]


CASES = _extended("py_ledger") + _extended("ts_cart")


class Tree:
    """A copy of a fixture's base tree with some overlay parts on top."""

    def __init__(self, root: Path, repo: str) -> None:
        self.root = root
        self.repo = repo
        shutil.copytree(FIXTURE_REPOS / repo / "base", root)
        if repo == "ts_cart":
            link_dir(FIXTURE_NODE_MODULES.resolve(), root / "node_modules")

    def overlay(self, variant_dir: Path, part: str) -> Tree:
        for path in (variant_dir / part).rglob("*"):
            if path.is_file():
                target = self.root / path.relative_to(variant_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(path, target)
        return self

    def run(self, *files: str) -> tuple[int, str]:
        if self.repo == "py_ledger":
            env = dict(os.environ, PYTHONPATH=str(self.root / "src"), PYTHONDONTWRITEBYTECODE="1")
            command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE"]
        else:
            env = dict(os.environ)
            vitest = FIXTURE_NODE_MODULES / "vitest" / "vitest.mjs"
            command = [shutil.which("node") or "node", str(vitest), "run", "--reporter=verbose"]
        proc = subprocess.run(
            [*command, *files],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def close(self) -> None:
        if self.repo == "ts_cart":
            unlink_all(self.root)


@pytest.fixture
def trees(tmp_path: Path) -> Iterator[Any]:
    made: list[Tree] = []

    def make(repo: str) -> Tree:
        tree = Tree(tmp_path / f"t{len(made)}", repo)
        made.append(tree)
        return tree

    yield make
    for tree in made:
        tree.close()


def _test_file(repo: str, task: str) -> str:
    return next(f for f in EXPECTATIONS[repo]["tasks"][task]["files"] if f.startswith("tests/"))


@pytest.mark.parametrize(("repo", "task"), CASES)
def test_extended_variants_are_green_and_their_tests_are_red_on_base(
    trees: Any, repo: str, task: str
) -> None:
    task_dir = FIXTURE_REPOS / repo / "tasks" / task
    test_file = _test_file(repo, task)
    for variant, spec in EXPECTATIONS[repo]["tasks"][task]["variants"].items():
        overlay = task_dir / variant
        code, out = trees(repo).overlay(overlay, "src").overlay(overlay, "tests").run()
        assert code == 0, f"{variant} head is not green\n{out[-1500:]}"
        code, out = trees(repo).overlay(overlay, "tests").run(test_file)
        assert code != 0, f"{variant}: its tests all pass on the base source"
        for nodeid in spec["red_on_base"]:
            name = nodeid.split("::", 1)[1].rsplit(" > ", 1)[-1]
            assert name in out, f"{variant}: {nodeid} did not run on base\n{out[-1500:]}"


@pytest.mark.parametrize(("repo", "task"), CASES)
def test_extended_gamed_variants_are_wrong_by_construction(
    trees: Any, repo: str, task: str
) -> None:
    task_dir = FIXTURE_REPOS / repo / "tasks" / task
    test_file = _test_file(repo, task)
    for gamed in ("hardcoded", "partial"):
        tree = trees(repo).overlay(task_dir / gamed, "src").overlay(task_dir / "honest", "tests")
        code, out = tree.run(test_file)
        assert code != 0, f"{gamed} passes every honest test, so it is not actually wrong"
    code, out = trees(repo).overlay(task_dir / "weakened", "src").run(test_file)
    assert code != 0, "the weakened source passes every base test, so nothing was hidden"
    weakened = EXPECTATIONS[repo]["tasks"][task]["variants"]["weakened"]["weakened_test"]
    assert weakened.split("::", 1)[1].rsplit(" > ", 1)[-1] in out


def test_extended_fixture_files_match_their_source_data() -> None:
    script = TESTS_DIR / "golden" / "make_c1_tasks.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
