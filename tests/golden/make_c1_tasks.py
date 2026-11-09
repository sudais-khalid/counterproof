"""Writes the extended tier of corpus C1 (tasks T6 to T20 of py_ledger and ts_cart).

The task data lives in c1_tasks_py.py and c1_tasks_ts.py. This script turns it into the
fixture layout (a base module and test file per new module, four variant overlays per task)
and prints the matching tests/golden/expectations.yaml entries. Re-running it rewrites the
same files; ``--check`` only verifies that the files on disk match the data.

    uv run python tests/golden/make_c1_tasks.py [--check] [--yaml]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import c1_tasks_py  # noqa: E402
import c1_tasks_ts  # noqa: E402

REPOS = HERE.parent / "fixtures" / "repos"
_TEST_NAME = re.compile(r"(?:def (test_\w+)\(|(?:it|test)\(\"([^\"]+)\")")


class Layout:
    def __init__(self, repo: str, data: Any) -> None:
        self.repo = repo
        self.data = data
        self.python = repo == "py_ledger"

    def source(self, module: str) -> str:
        return f"src/ledger/{module}.py" if self.python else f"src/cart/{module}.ts"

    def tests(self, module: str) -> str:
        return f"tests/test_{module}.py" if self.python else f"tests/{module}.test.ts"

    def nodeid(self, module: str, name: str) -> str:
        if self.python:
            return f"{self.tests(module)}::{name}"
        return f"{self.tests(module)}::{self.data.SUITES[module]} > {name}"


def _replace(text: str, old: str, new: str, what: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{what}: expected exactly one match, found {count}:\n{old}")
    return text.replace(old, new)


def _names(block: str) -> list[str]:
    return [a or b for a, b in _TEST_NAME.findall(block)]


def _with_tests(layout: Layout, tests: str, added: str) -> str:
    if layout.python:
        return tests.rstrip("\n") + "\n" + added
    # TS: new cases go inside the module's describe block, before its closing line.
    body, sep, tail = tests.rstrip("\n").rpartition("\n});")
    if not sep:
        raise SystemExit("TS test file must end with a describe block")
    return body + "\n" + added.rstrip("\n") + "\n});" + tail + "\n"


def variants(layout: Layout, task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    module = task["module"]
    base_src, base_tests = layout.data.MODULES[module]
    tid = task["id"]

    def src(old: str, new: str) -> str:
        text = _replace(base_src, old, new, f"{tid} source")
        if task.get("imports_old"):
            added = set(re.findall(r"\w+", task["imports_new"])) - set(
                re.findall(r"\w+", task["imports_old"])
            )
            if any(name in new for name in added):  # the fix uses a newly imported name
                text = _replace(text, task["imports_old"], task["imports_new"], f"{tid} imports")
        return text

    example = task["example"]
    example_names = _names(example)
    honest_names = example_names + list(task["honest_red"])
    weakened_tests = _replace(
        base_tests, task["weaken_old"], task["weaken_new"], f"{tid} weakened tests"
    )
    honest_src = src(task["fix_old"], task["honest"])
    out: dict[str, dict[str, Any]] = {
        "honest": {
            "src": honest_src,
            "tests": _with_tests(layout, base_tests, example + task["honest_tests"]),
            "red": honest_names,
        },
        "hardcoded": {
            "src": _replace(base_src, task["fix_old"], task["hardcoded"], f"{tid} hardcoded"),
            "tests": _with_tests(layout, base_tests, example),
            "red": example_names,
        },
        "weakened": {
            "src": src(task["drop_old"], task["drop_new"]),
            "tests": _with_tests(layout, weakened_tests, example),
            "red": example_names,
        },
        "partial": {
            "src": src(task["fix_old"], task["partial"]),
            "tests": _with_tests(layout, base_tests, example),
            "red": example_names,
        },
    }
    if out["partial"]["src"] == honest_src or out["weakened"]["src"] == base_src:
        raise SystemExit(f"{tid}: a gamed variant equals another variant")
    return out


def _files(layout: Layout) -> dict[Path, str]:
    root = REPOS / layout.repo
    files: dict[Path, str] = {}
    for module, (src, tests) in layout.data.MODULES.items():
        files[root / "base" / layout.source(module)] = src
        files[root / "base" / layout.tests(module)] = tests
    for task in layout.data.TASKS:
        task_dir = root / "tasks" / task["id"]
        files[task_dir / "task.md"] = task["text"] + "\n"
        for name, variant in variants(layout, task).items():
            files[task_dir / name / layout.source(task["module"])] = variant["src"]
            files[task_dir / name / layout.tests(task["module"])] = variant["tests"]
    return files


def _flow(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def yaml_entries(layout: Layout) -> str:
    lines: list[str] = []
    for task in layout.data.TASKS:
        module = task["module"]
        found = variants(layout, task)

        def red(name: str, found: dict[str, dict[str, Any]] = found, module: str = module) -> str:
            return _flow([layout.nodeid(module, n) for n in found[name]["red"]])

        weakened = layout.nodeid(module, task["weakened_test"])
        lines += [
            f"      {task['id']}:",
            "        intent: bugfix",
            "        tier: extended",
            f"        files: [{layout.source(module)}, {layout.tests(module)}]",
            "        variants:",
            "          honest: {verdict: PROVEN, rule_id: R-PROVEN, "
            f"red_on_base: {red('honest')}}}",
            "          hardcoded: {verdict: REFUTED, rule_id: R-REF-INTEGRITY, "
            f"integrity_rule: FR-IN-07, red_on_base: {red('hardcoded')}}}",
            "          weakened: {verdict: REFUTED, rule_id: R-REF-INTEGRITY, "
            f'integrity_rule: {task["weaken_rule"]}, weakened_test: "{weakened}", '
            f"red_on_base: {red('weakened')}}}",
            "          partial: {verdict: [UNPROVEN, WEAK], rule_id: null, "
            f"red_on_base: {red('partial')}}}",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify, write nothing")
    parser.add_argument("--yaml", action="store_true", help="print expectations entries")
    args = parser.parse_args()
    stale: list[str] = []
    for layout in (Layout("py_ledger", c1_tasks_py), Layout("ts_cart", c1_tasks_ts)):
        for path, text in _files(layout).items():
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current == text:
                continue
            if args.check:
                stale.append(str(path.relative_to(REPOS)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
        if args.yaml:
            print(f"# {layout.repo}")
            print(yaml_entries(layout), end="")
    if stale:
        print("out of date:\n  " + "\n  ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
