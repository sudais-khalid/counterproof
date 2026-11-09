"""P4 blind oracle for TypeScript and JavaScript: FR-BO-01 interface (never a body, asserted
on the prompt itself), FR-BO-02 validation of model-written tests, FR-BO-06 export. Fake
providers; no network and no Node (the real-runner tests are in tests/integration)."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from _pytest.mark.structures import ParameterSet

from counterproof import git
from counterproof.change.resolver import resolve_change
from counterproof.change.task import TaskText
from counterproof.llm.budget import BudgetGuard
from counterproof.llm.cache import LLMCache
from counterproof.llm.roles import LLMSession, load_prompt
from counterproof.llm.schemas import OracleOutput, OracleTest
from counterproof.models import ChangeSet
from counterproof.probes import tsast
from counterproof.probes.blind_oracle import export as oracle_export
from counterproof.probes.blind_oracle.interface import Interface
from counterproof.probes.blind_oracle.interface_ts import (
    CHANGED_TS,
    HIDDEN,
    changed_function_sources_ts,
    extract_interface_ts,
    resolve,
    specifier_from,
)
from counterproof.probes.blind_oracle.probe import evidence_name
from counterproof.probes.blind_oracle.triage import platform_neutral
from counterproof.probes.blind_oracle.writer import oracle_filename, write_oracle_tests
from counterproof.probes.blind_oracle.writer_ts import FRAMEWORK_IMPORTS, validate_ts
from tests.helpers import all_variants, build_repo
from tests.helpers import git as run_git
from tests.unit.test_llm import FakeProvider

CART_TESTS = [
    "tests/discount.test.ts",
    "tests/money.test.ts",
    "tests/offers.test.ts",
    "tests/rounding.test.ts",
    "tests/tax.test.ts",
]
WRITER_REPLY = json.dumps(
    {
        "imports": ['import { sum } from "../src/cart/money";'],
        "tests": [
            {
                "name": "adds",
                "intent": "adds",
                "code": 'test("adds", () => {\n  expect(sum([1, 2])).toBe(3);\n});\n',
            }
        ],
    }
)
IFACE = Interface(
    modules={"../src/cart/money": "export function sum(values: number[]): number { }\n"},
    changed_symbols={"../src/cart/money": ["sum"]},
    public_names={"../src/cart/money": frozenset({"sum", "splitEvenly", "Cents"})},
    test_dir="tests",
    language="typescript",
    file_suffix=".test.ts",
    framework="vitest",
)


def _session(replies: list[str]) -> tuple[LLMSession, FakeProvider]:
    provider = FakeProvider(list(replies))
    return (
        LLMSession(
            provider=provider,
            cache=LLMCache(Path("unused"), "off"),
            budget=BudgetGuard(max_tokens=10**6, max_usd=1.0, max_seconds=600),
            prices={},
        ),
        provider,
    )


def _change(tmp_path: Path, repo_name: str, task: str, variant: str) -> ChangeSet:
    repo = build_repo(tmp_path / "repo", name=repo_name, task=task, variant=variant)
    assert repo.head_sha is not None
    return resolve_change(
        repo.path,
        base_sha=repo.base_sha,
        head_ref=repo.head_sha,
        head_sha=repo.head_sha,
        task=TaskText(repo.task_text, "cli"),
    )


def _readers(change: ChangeSet) -> tuple[Any, Any]:
    return (
        partial(git.show_file, change.repo_root, change.head_snapshot_sha),
        partial(git.show_file, change.repo_root, change.base_sha),
    )


def _body_lines(path: str, text: str) -> set[str]:
    """Every line inside any function body of a TS/JS module (the braces excluded). Bare
    identifiers (an arrow body such as ``share``) are skipped: they also occur in prose."""
    tree = tsast.parse(path, text)
    assert tree is not None
    lines: set[str] = set()
    for node in tsast.walk(tree.root_node):
        if node.type in tsast.FUNCTION_NODES:
            body = node.child_by_field_name("body")
            if body is None:
                continue
            for child in body.named_children if body.type == "statement_block" else [body]:
                for line in tsast.text_of(child).splitlines():
                    stripped = line.strip()
                    if len(stripped) >= 4 and not stripped.isidentifier():
                        lines.add(stripped)
    return lines


def _output(tests: list[tuple[str, str]], imports: list[str] | None = None) -> OracleOutput:
    return OracleOutput(
        imports=imports if imports is not None else ['import { sum } from "../src/cart/money";'],
        tests=[OracleTest(name=n, intent=f"checks {n}", code=c) for n, c in tests],
    )


# -- FR-BO-01: what the oracle sees --------------------------------------------------------


@pytest.mark.parametrize(
    ("repo_name", "task", "variant"),
    [
        *[
            pytest.param("ts_cart", *p.values, marks=p.marks, id=p.id)
            if isinstance(p, ParameterSet)
            else ("ts_cart", *p)
            for p in all_variants("ts_cart")
        ],
        ("js_jest_mini", "T1-reserve-all", "honest"),
        ("js_jest_mini", "T1-reserve-all", "hardcoded"),
    ],
)
def test_fr_bo_01_ts_oracle_prompt_never_contains_a_function_body(
    tmp_path: Path, repo_name: str, task: str, variant: str
) -> None:
    """The prompt actually sent to the oracle model holds no body line of any function in any
    TS/JS source module (changed or imported), on every fixture variant."""
    change = _change(tmp_path, repo_name, task, variant)
    read_head, read_base = _readers(change)
    tests = [f.path for f in change.files if f.role == "test"]
    interface = extract_interface_ts(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=tests or CART_TESTS,
        framework="jest" if repo_name == "js_jest_mini" else "vitest",
    )
    session, provider = _session([WRITER_REPLY])
    write_oracle_tests(session, models=["m"], task_text=change.task_text, interface=interface, k=6)
    (request,) = provider.requests
    prompt = request.system + "\n" + request.user

    assert interface.changed_symbols and CHANGED_TS in prompt and HIDDEN in prompt
    assert interface.language == "typescript"
    sources = run_git(change.repo_root, "ls-tree", "-r", "--name-only", change.head_snapshot_sha)
    leaked = []
    for path in sources.splitlines():
        if path.startswith("src/") and tsast.is_ts_path(path):
            leaked += [ln for ln in _body_lines(path, read_head(path) or "") if ln in prompt]
    assert leaked == []
    assert interface.style_path is not None
    base_tests = read_base(interface.style_path) or ""
    head_tests = read_head(interface.style_path) or ""
    agents = [
        ln.strip()
        for ln in head_tests.splitlines()
        if ln.strip().startswith(("it(", "test(")) and ln not in base_tests
    ]
    assert [ln for ln in agents if ln in prompt] == []


def test_fr_bo_01_ts_interface_keys_are_specifiers_and_mark_changes(tmp_path: Path) -> None:
    change = _change(tmp_path, "ts_cart", "T1-discount-cap", "honest")
    read_head, read_base = _readers(change)
    interface = extract_interface_ts(
        change, read_head=read_head, read_base=read_base, selected_test_files=CART_TESTS
    )
    assert interface.changed_symbols == {"../src/cart/discount": ["percentOff"]}
    # The relative import of the changed module is stubbed too, so inputs can be built.
    assert set(interface.modules) == {"../src/cart/discount", "../src/cart/money"}
    assert interface.public_names["../src/cart/money"] >= {"Cents", "sum", "splitEvenly"}
    assert interface.style_path == "tests/discount.test.ts"
    assert interface.test_dir == "tests" and interface.file_suffix == ".test.ts"
    assert oracle_filename(interface) == "counterproof_oracle.test.ts"
    assert evidence_name(interface) == "test_oracle.ts"
    assert set(changed_function_sources_ts(change, read_head)) == {"percentOff"}


def test_fr_bo_01_ts_excluded_paths_never_reach_the_interface(tmp_path: Path) -> None:
    change = _change(tmp_path, "ts_cart", "T1-discount-cap", "honest")
    read_head, read_base = _readers(change)
    interface = extract_interface_ts(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=CART_TESTS,
        sendable=lambda p: not p.endswith("money.ts"),
    )
    assert "../src/cart/money" not in interface.modules
    assert changed_function_sources_ts(change, read_head, sendable=lambda _p: False) == {}


def test_fr_bo_01_ts_stub_hides_bodies_private_members_and_computed_values() -> None:
    source = (
        "/** Prices. */\n"
        'import { helper } from "./helper";\n'
        "export const RATE = 20;\n"
        "export const TABLE = buildTable();\n"
        "export interface Line { sku: string; cents: number }\n"
        "/** Adds tax. */\n"
        "export function gross(net: number): number {\n  return net + secret(net);\n}\n"
        "export const half = (x: number): number => x / 2;\n"
        "export class Till {\n  private hidden = 1;\n  #count = 0;\n  total = 0;\n"
        "  add(cents: number): void {\n    this.#count += cents;\n  }\n"
        "  _internal(): number {\n    return 42;\n  }\n}\n"
        "function secret(x: number): number {\n  return x * 7;\n}\n"
    )
    files = {
        "src/price.ts": source,
        "src/helper.ts": "export function helper() {\n  return 1;\n}\n",
    }
    change = ChangeSet.model_construct(
        files=[type("F", (), {"path": "src/price.ts", "role": "source", "status": "modified"})()],
        hunks=[],
    )
    interface = extract_interface_ts(
        change,
        read_head=files.get,
        read_base=files.get,
        selected_test_files=["tests/price.test.ts"],
    )
    stub = interface.modules["../src/price"]
    for hidden in ("secret", "return", "x * 7", "net + ", "#count", "private hidden", "_internal"):
        assert hidden not in stub, hidden
    assert "export const RATE = 20;" in stub
    assert "export declare const TABLE;" in stub and "buildTable" not in stub
    assert "export interface Line { sku: string; cents: number }" in stub
    assert "/** Adds tax. */" in stub
    assert f"export function gross(net: number): number {HIDDEN}" in stub
    assert f"export const half = (x: number): number => {HIDDEN};" in stub
    assert "total = 0;" in stub and f"add(cents: number): void {HIDDEN}" in stub
    assert interface.public_names["../src/price"] == {
        "RATE",
        "TABLE",
        "Line",
        "gross",
        "half",
        "Till",
    }
    assert "../src/helper" in interface.modules


@pytest.mark.parametrize(
    ("test_dir", "path", "spec"),
    [
        ("tests", "src/cart/money.ts", "../src/cart/money"),
        ("src/cart", "src/cart/money.ts", "./money"),
        ("src/cart/__tests__", "src/cart/money.ts", "../money"),
        ("test", "src/stock.js", "../src/stock"),
        ("", "lib/index.ts", "./lib"),
        ("tests/unit", "src/a/index.tsx", "../../src/a"),
    ],
)
def test_fr_bo_01_specifiers_are_relative_to_the_oracle_directory(
    test_dir: str, path: str, spec: str
) -> None:
    assert specifier_from(test_dir, path) == spec


def test_fr_bo_01_relative_imports_resolve_to_repository_files() -> None:
    files = {"src/cart/money.ts": "x", "src/cart/index.ts": "x", "src/util/fmt.js": "x"}
    assert resolve("src/cart/tax.ts", "./money", files.get) == "src/cart/money.ts"
    assert resolve("src/cart/tax.ts", "./money.js", files.get) == "src/cart/money.ts"
    assert resolve("src/app.ts", "./cart", files.get) == "src/cart/index.ts"
    assert resolve("src/cart/tax.ts", "../util/fmt", files.get) == "src/util/fmt.js"
    assert resolve("src/cart/tax.ts", "lodash", files.get) is None
    assert resolve("src/cart/tax.ts", "./missing", files.get) is None


# -- FR-BO-02: validation of model-written TS tests ----------------------------------------


def test_fr_bo_02_ts_valid_tests_are_renamed_and_get_an_intent_line() -> None:
    out = _output(
        [
            ("adds", 'test("adds two", () => {\n  expect(sum([1, 2])).toBe(3);\n});'),
            (
                "adds",
                'it("adds two", () => {\n  const parts = [1, 2];\n'
                "  expect(sum(parts)).toBe(3);\n});",
            ),
        ],
        imports=[
            'import { sum } from "../src/cart/money";',
            'import { expect, test } from "vitest";',
        ],
    )
    suite = validate_ts(out, IFACE, k=6, framework="vitest")
    assert suite.rejected == []
    assert [c.name for c in suite.cases] == ["test_oracle_adds_two", "test_oracle_adds_two_2"]
    assert suite.cases[0].code.startswith("// intent: checks adds\n")
    assert 'test("test_oracle_adds_two", () =>' in suite.cases[0].code
    # The framework import is Counterproof's own; the model's copy is dropped.
    assert suite.imports == [
        FRAMEWORK_IMPORTS["vitest"],
        'import { sum } from "../src/cart/money";',
    ]
    text = suite.text("Blind-oracle tests")
    assert text.startswith("/** Blind-oracle tests */\n")
    assert tsast.parse("x.test.ts", text) is not None
    jest = validate_ts(out, IFACE, k=1, framework="jest")
    assert jest.imports == ['import { sum } from "../src/cart/money";'] and len(jest.cases) == 1


def test_fr_bo_02_ts_commonjs_imports_are_accepted() -> None:
    iface = Interface(
        modules={"../src/stock": "x"},
        public_names={"../src/stock": frozenset({"reserve"})},
        changed_symbols={"../src/stock": ["reserve"]},
        language="typescript",
        file_suffix=".test.js",
        framework="jest",
    )
    out = _output(
        [("r", 'test("r", () => {\n  expect(reserve(10, 3)).toBe(7);\n});')],
        imports=['const { reserve } = require("../src/stock");'],
    )
    suite = validate_ts(out, iface, k=6, framework="jest")
    assert suite.rejected == [] and suite.imports == [
        'const { reserve } = require("../src/stock");'
    ]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ('describe("d", () => {\n  test("x", () => {});\n});', "exactly one top-level test"),
        ('test("a", () => {});\ntest("b", () => {});', "exactly one top-level test"),
        ('test.only("a", () => {});', "exactly one top-level test"),
        ("const x = 1;", "exactly one top-level test"),
        ('test("a", () => {', "syntax error"),
        ("test(name, () => {});", "string name"),
        ('test("a", () => { require("fs"); });', "uses require"),
        ('test("a", () => { process.exit(1); });', "uses process"),
        ('test("a", () => { vi.mock("../src/cart/money"); });', "uses vi"),
        ('test("a", () => { expect(Math.random()).toBe(1); });', "uses Math.random"),
        ('test("a", () => { expect(Date.now()).toBe(1); });', "uses Date.now"),
        ('test("a", () => { setTimeout(() => {}, 1); });', "uses setTimeout"),
        ('test("a", () => { fetch("http://x"); });', "uses fetch"),
        ('test("a", async () => { await import("fs"); });', "imports inside a test"),
        ('test("a", () => { sum._cache = 1; });', "uses property _cache"),
        ('test("a", () => { sum.constructor("return 1")(); });', "uses property constructor"),
        ('test("a", () => { expect(sum["call"]).toBeDefined(); });', "computed member"),
        (
            'test("a", () => { expect(splitEvenly(1, 1)).toEqual([1]); });',
            "undefined name splitEvenly",
        ),
        ('test("a", () => { expect(total).toBe(1); });', "undefined name total"),
    ],
)
def test_fr_bo_02_ts_invalid_tests_are_rejected(code: str, reason: str) -> None:
    suite = validate_ts(_output([("t", code)]), IFACE, k=6, framework="vitest")
    assert suite.cases == []
    assert len(suite.rejected) == 1 and reason in suite.rejected[0][1], suite.rejected


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ('import { readFileSync } from "fs";', "not in the interface"),
        ('import { sum } from "../src/cart/tax";', "not in the interface"),
        ('import { secret } from "../src/cart/money";', "secret from ../src/cart/money"),
        ('import { _round } from "../src/cart/money";', "private name _round"),
        ('import * as money from "../src/cart/money";', "namespace or default"),
        ('import money from "../src/cart/money";', "namespace or default"),
        ('const fs = require("fs");', "not an import"),
        ("import { a } from 'x'; import { b } from 'y';", "not a single import"),
        ("sum([1])", "not an import"),
    ],
)
def test_fr_bo_02_ts_imports_are_checked(line: str, reason: str) -> None:
    suite = validate_ts(_output([], imports=[line]), IFACE, k=6, framework="vitest")
    assert any(reason in r for _, r in suite.rejected), suite.rejected


def test_fr_bo_02_ts_bound_names_are_not_undefined() -> None:
    code = (
        'test("a", () => {\n'
        "  const [first, ...rest] = [1, 2, 3];\n"
        "  const { length } = rest;\n"
        "  const twice = (n: number) => n * 2;\n"
        "  function pick(xs: number[], i: number) { return xs[i]; }\n"
        "  try { sum([first]); } catch (err) { expect(err).toBeUndefined(); }\n"
        "  for (const v of rest) { expect(v).toBeGreaterThan(0); }\n"
        "  const obj = { length };\n"
        "  expect(pick(rest, 0) + twice(first) + obj.length).toBe(6);\n"
        "  expect(() => sum([1.5])).toThrow(RangeError);\n"
        "});"
    )
    suite = validate_ts(_output([("a", code)]), IFACE, k=6, framework="vitest")
    assert suite.rejected == [] and len(suite.cases) == 1


def test_fr_bo_02_ts_prompt_files_and_contract() -> None:
    writer = load_prompt("oracle_writer", variant="typescript")
    assert writer.version == "v1.typescript"
    for placeholder in ("{{task}}", "{{interface}}", "{{style}}", "{{k}}"):
        assert placeholder in writer.user
    assert "{{framework}}" in writer.system and "// intent:" in writer.system
    triage = load_prompt("triage_judge", variant="typescript")
    assert triage.version == "v2.typescript" and "```typescript" in triage.user


def test_triage_paths_are_platform_neutral_for_ts_files() -> None:
    text = "at tests\\counterproof_oracle.test.ts:5:7\r\nat src\\cart\\money.mts:2:1\r\n"
    assert platform_neutral(text) == (
        "at tests/counterproof_oracle.test.ts:5:7\nat src/cart/money.mts:2:1\n"
    )


# -- FR-BO-06: export ----------------------------------------------------------------------


def test_fr_bo_06_ts_export_writes_a_test_file_of_the_same_flavour(tmp_path: Path) -> None:
    suite = validate_ts(
        _output([("adds", 'test("adds", () => {\n  expect(sum([1, 2])).toBe(3);\n});')]),
        IFACE,
        k=6,
        framework="vitest",
    )
    record = {
        "language": "typescript",
        "suffix": ".test.ts",
        "test_dir": "tests",
        "imports": suite.imports,
        "tests": [
            {"name": c.name, "intent": c.intent, "code": c.code, "status": "agree"}
            for c in suite.cases
        ],
    }
    target, names = oracle_export.export(record, "20261108T000000Z-abc123", ["all"], tmp_path)
    assert target.name == "counterproof_oracle_abc123.test.ts"
    assert names == ["test_oracle_adds"]
    text = target.read_text(encoding="utf-8")
    assert text.startswith("/** Blind-oracle tests accepted from Counterproof run")
    assert FRAMEWORK_IMPORTS["vitest"] in text and tsast.parse(target.name, text) is not None
