"""Node adapters without running Node: FR-AD-02/03 result mapping, FR-AD-04 JS failures,
FR-AD-05 no installs, FR-AD-06 adapter choice, worktree links and related tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from counterproof.adapters.base import refine_api_absent
from counterproof.adapters.detect import choose_adapter, detect_adapters
from counterproof.adapters.jest_adapter import JestAdapter
from counterproof.adapters.junit import JUnitCase
from counterproof.adapters.node import (
    ensure_node_modules,
    js_regex_escape,
    name_pattern,
    related_by_imports,
    resolve_import,
)
from counterproof.adapters.vitest_adapter import VitestAdapter
from counterproof.models import ChangedFile, ChangeSet
from counterproof.workspace.links import is_dir_link, link_dir, unlink_all
from counterproof.workspace.manager import Workspace


def _case(
    classname: str, name: str, outcome: str = "pass", message: str = "", text: str = ""
) -> JUnitCase:
    return JUnitCase(classname, name, outcome, message, text, 0.01)  # type: ignore[arg-type]


def test_name_patterns_are_anchored_and_escaped() -> None:
    assert js_regex_escape("a.b (c) [d] + e?") == r"a\.b \(c\) \[d\] \+ e\?"
    assert name_pattern(["t > b", "t > a"]) == r"^(?:t > a|t > b)$"


def test_vitest_results_map_to_node_ids_and_load_errors_to_collection() -> None:
    adapter = VitestAdapter(Path(), source_env={})
    tests = [
        "tests/a.test.ts::s > ok",
        "tests/a.test.ts::s > bad",
        "tests/b.test.ts::x",
        "tests/a.test.ts::s > missing",
    ]
    cases = [
        _case("tests/a.test.ts", "s > ok"),
        _case(
            "tests/a.test.ts",
            "s > bad",
            "fail",
            "expected 3 to be 4",
            "AssertionError: expected 3 to be 4",
        ),
        _case(
            "tests/b.test.ts",
            "tests/b.test.ts",
            "fail",
            "Transform failed with 1 error",
            "[PARSE_ERROR]",
        ),
    ]
    outcomes, failures, _ = adapter._map_results(cases, tests, timed_out=False, fail_fast=False)
    assert outcomes == {
        "tests/a.test.ts::s > ok": "pass",
        "tests/a.test.ts::s > bad": "fail",
        "tests/b.test.ts::x": "error",
        "tests/a.test.ts::s > missing": "error",
    }
    assert failures["tests/a.test.ts::s > bad"].kind == "ASSERTION"
    assert failures["tests/b.test.ts::x"].kind == "COLLECTION"
    # --bail: tests that never ran are not reported as failures
    outcomes, _, _ = adapter._map_results(
        cases[:2], [*tests[:2], tests[3]], timed_out=False, fail_fast=True
    )
    assert "tests/a.test.ts::s > missing" not in outcomes


def test_jest_results_strip_the_empty_ancestor_and_normalise_paths() -> None:
    adapter = JestAdapter(Path(), source_env={})
    tests = ["test/a.test.js::top", "test/a.test.js::d > inner", "test/b.test.js::x"]
    cases = [
        _case("test\\a.test.js", " > top"),
        _case("test\\a.test.js", "d > inner", "fail", "boom", "TypeError: boom"),
        _case("Test suite failed to run", "test\\b.test.js", "error", "", "SyntaxError"),
    ]
    outcomes, failures, _ = adapter._map_results(cases, tests, timed_out=False, fail_fast=False)
    assert outcomes == {
        "test/a.test.js::top": "pass",
        "test/a.test.js::d > inner": "fail",
        "test/b.test.js::x": "error",
    }
    assert failures["test/b.test.js::x"].kind == "COLLECTION"
    assert adapter._targets(["test/a.test.js::d > inner"]) == [
        "--runTestsByPath",
        "test/a.test.js",
        "-t",
        "^(?:d inner)$",
    ]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("TypeError: (0 , __vi_import_0__.splitCents) is not a function", "API_ABSENT"),
        (
            "SyntaxError: The requested module './money' does not provide an export named 'splitCents'",
            "API_ABSENT",
        ),
        (
            "Error: Failed to load url ../src/cart/coupon (resolved id: ../src/cart/coupon)",
            "API_ABSENT",
        ),
        ("TypeError: (0 , __vi_import_0__.total) is not a function", "EXCEPTION_BEHAVIOURAL"),
        ("RangeError: parts must be positive", "EXCEPTION_BEHAVIOURAL"),
    ],
)
def test_fr_ad_04_api_absent_for_javascript_messages(text: str, kind: str) -> None:
    added = {"splitCents", "splitCents(parts)"}
    assert refine_api_absent("EXCEPTION_BEHAVIOURAL", text, added, {"src.cart.coupon"}) == kind


def test_fr_ad_05_missing_node_modules_is_an_actionable_message(tmp_path: Path) -> None:
    ws = Workspace("head", tmp_path / "ws", "x")
    ws.root.mkdir()
    message = ensure_node_modules(tmp_path / "repo", ws)
    assert message is not None and "npm ci" in message and "FR-AD-05" in message


def test_worktree_links_never_touch_the_real_node_modules(tmp_path: Path) -> None:
    real = tmp_path / "repo" / "node_modules"
    (real / "vitest").mkdir(parents=True)
    (real / "vitest" / "package.json").write_text("{}", encoding="utf-8")
    ws = Workspace("head", tmp_path / "ws", "x")
    ws.root.mkdir()
    assert ensure_node_modules(tmp_path / "repo", ws) is None
    assert is_dir_link(ws.root / "node_modules")
    assert (ws.root / "node_modules" / "vitest" / "package.json").is_file()
    assert ensure_node_modules(tmp_path / "repo", ws) is None  # idempotent
    unlink_all(ws.root)
    assert not (ws.root / "node_modules").exists()
    assert (real / "vitest" / "package.json").is_file()


def test_link_dir_round_trip(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    link_dir(tmp_path / "src", tmp_path / "link")
    assert is_dir_link(tmp_path / "link") and not is_dir_link(tmp_path / "src")


def test_related_tests_follow_relative_imports_transitively() -> None:
    files = {
        "tests/a.test.ts": 'import { f } from "../src/a";',
        "tests/b.test.ts": 'import { g } from "../src/b.js";',
        "tests/c.test.ts": 'import { h } from "lodash";',
        "src/a.ts": 'export { x } from "./lib/x";\nexport function f() {}',
        "src/lib/x.ts": "export const x = 1;",
        "src/b.ts": "export function g() {}",
    }
    assert resolve_import("tests/b.test.ts", "../src/b.js", lambda p: p in files) == "src/b.ts"
    assert resolve_import("tests/c.test.ts", "lodash", lambda p: p in files) is None
    related = related_by_imports(
        sorted(f for f in files if f.startswith("tests/")), ["src/lib/x.ts"], files.get
    )
    assert related == {"tests/a.test.ts"}


def _write_package(root: Path, deps: dict[str, str], *, config: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"devDependencies": deps}), encoding="utf-8")
    if config:
        (root / config).write_text("export default {}", encoding="utf-8")


def test_fr_ad_06_detection_and_adapter_choice(tmp_path: Path) -> None:
    _write_package(tmp_path / "vt", {"vitest": "5"})
    _write_package(tmp_path / "jt", {"jest": "30", "jest-junit": "17"})
    _write_package(tmp_path / "both", {"vitest": "5", "jest": "30"}, config="jest.config.js")
    assert [a.name for a in detect_adapters(tmp_path / "vt")] == ["vitest"]
    assert [a.name for a in detect_adapters(tmp_path / "jt")] == ["jest"]
    assert next(a.name for a in detect_adapters(tmp_path / "both")) == "jest"
    mixed = tmp_path / "mixed"
    _write_package(mixed, {"vitest": "5"})
    (mixed / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    adapters = detect_adapters(mixed)
    assert {a.name for a in adapters} == {"pytest", "vitest"}

    def change(*paths: str) -> ChangeSet:
        return ChangeSet(
            repo_root=mixed,
            base_sha="a",
            head_ref="b",
            head_snapshot_sha="b",
            files=[
                ChangedFile(path=p, role="test" if "test" in p else "source", status="modified")
                for p in paths
            ],
            intent="bugfix",
            intent_source="rule",
        )

    assert choose_adapter(adapters, change("src/cart.ts", "tests/cart.test.ts")).name == "vitest"  # type: ignore[union-attr]
    assert choose_adapter(adapters, change("pkg/mod.py")).name == "pytest"  # type: ignore[union-attr]
