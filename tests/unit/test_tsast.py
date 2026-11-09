"""TypeScript/JavaScript analysis (tree-sitter): test files, symbols, imports, mutants,
scope and the TS integrity rules (FR-IN-01 to FR-IN-07, FR-MU-01, FR-MU-02, FR-MU-06/07)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from counterproof.probes import lang, tsast
from counterproof.probes.integrity.overfit_ts import overfit_findings_ts
from counterproof.probes.integrity.python_rules import (
    ChangedSource,
    TestFilePair,
    changed_test_ids,
    compare_test_files,
)
from counterproof.probes.mutants.apply import invalid_reason
from counterproof.probes.mutants.rule_forge_ts import generate_ts, mutation_scope_ts

TESTS = dedent(
    """\
    import { describe, it, expect, test, vi } from "vitest";
    import { total } from "../src/cart/money";
    import * as money from "../src/cart/money";

    describe("total", () => {
      it("adds prices", () => {
        expect(total([100, 250])).toBe(350);
      });
      it.skip("is skipped", () => {
        expect(total([1])).toBeTruthy();
      });
      describe("nested", () => {
        test("empty", () => {
          expect(total([])).toEqual(0);
        });
      });
    });

    test("spies on the unit", () => {
      vi.spyOn(money, "total");
      expect(() => total(null as never)).toThrow();
    });
    xit("old", () => {});
    """
)


def _tests(text: str = TESTS) -> dict[str, tsast.TestFunction]:  # type: ignore[name-defined]
    analysed = tsast.analyse_test_file(text, "tests/money.test.ts")
    assert analysed is not None
    return analysed.tests


def test_test_file_names_follow_vitest_full_names() -> None:
    assert list(_tests()) == [
        "total > adds prices",
        "total > is skipped",
        "total > nested > empty",
        "spies on the unit",
        "old",
    ]


def test_assertions_skips_and_mocks_are_counted() -> None:
    tests = _tests()
    adds = tests["total > adds prices"]
    assert (adds.asserts, adds.eq_asserts, adds.truthy_or_in_asserts, adds.line) == (1, 1, 0, 6)
    assert tests["total > is skipped"].skips == {"test:skip"}
    assert tests["total > is skipped"].truthy_or_in_asserts == 1
    assert tests["old"].skips == {"test:skip"}
    spy = tests["spies on the unit"]
    assert spy.patch_targets == [(20, "money.total")] and spy.raises_without_match == 1


def test_literals_leave_out_titles_and_module_paths() -> None:
    analysed = tsast.analyse_test_file(TESTS, "tests/money.test.ts")
    assert analysed is not None
    assert ("num", "350") in analysed.literals
    assert ("seq", "(num:100,num:250)") in analysed.literals
    assert ("str", "adds prices") not in analysed.literals
    assert ("str", "../src/cart/money") not in analysed.literals


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("expect(true).toBe(true);", "assertion compares a constant with itself"),
        ("expect(1).toBeTruthy();", "assertion compares a constant with itself"),
        ("try { expect(f()).toBe(2); } catch {}", "assertion inside try/catch that swallows"),
        ("assert(true);", "assert of a constant"),
    ],
)
def test_fr_in_05_neutralised_assertions(body: str, reason: str) -> None:
    (fn,) = _tests(f'it("x", () => {{ {body} }});\n').values()
    assert fn.neutralised and fn.neutralised[0][1].startswith(reason)


def test_unparseable_files_give_no_result() -> None:
    assert tsast.analyse_test_file("it('x', () => { const = 1 });", "a.test.ts") is None
    assert tsast.parse("a.ts", "export function (") is None


SOURCE = dedent(
    """\
    import { assertCents, type Cents } from "./money";
    export type Rate = number;
    export const FREE = 5000;
    export function shipping(subtotal: Cents, express = false): Cents {
      if (subtotal < 0) throw new RangeError("negative"); // guard
      const fee = Math.round(subtotal * 0.05);
      return subtotal >= FREE ? 0 : Math.max(fee, 499);
    }
    export class Cart { add(item: string, qty: number) { return qty; } }
    """
)


def test_symbols_imports_and_module_names() -> None:
    assert tsast.top_level_definitions(SOURCE) == {"Rate", "FREE", "shipping", "Cart"}
    symbols = tsast.defined_symbols(SOURCE)
    assert {"shipping(subtotal)", "shipping(express)", "add(qty)", "Cart"} <= symbols
    assert tsast.added_symbols("export function shipping(subtotal) {}", SOURCE) >= {
        "shipping(express)",
        "Cart",
    }
    assert tsast.import_specifiers(SOURCE) == ["./money"]
    assert tsast.import_specifiers('const m = require("./a"); import("./b");', "x.js") == [
        "./a",
        "./b",
    ]
    assert tsast.module_name("src/cart/discount.ts") == "src.cart.discount"
    assert tsast.module_name("src/cart/index.ts") == "src.cart"


def test_normalised_tree_ignores_comments_and_whitespace() -> None:
    same = SOURCE.replace(" // guard", "").replace("  const fee", "    const   fee")
    assert tsast.normalised("a.ts", same) == tsast.normalised("a.ts", SOURCE)
    assert tsast.normalised("a.ts", SOURCE.replace(">=", ">")) != tsast.normalised("a.ts", SOURCE)
    assert invalid_reason(SOURCE, same, "src/a.ts") == "syntax tree unchanged"
    assert invalid_reason(SOURCE, SOURCE.replace("{", "", 1), "src/a.ts") == "does not parse"
    assert invalid_reason(SOURCE, SOURCE.replace(">=", ">"), "src/a.ts") is None


def test_enclosing_function_and_comment_stripping() -> None:
    fn = tsast.enclosing_function("a.ts", SOURCE, 6)
    assert fn.startswith("export function shipping(") and fn.endswith("}")
    assert "// guard" not in tsast.strip_comments("a.ts", SOURCE)
    assert lang.language_name("a.ts") == "typescript" and lang.language_name("a.js") == "javascript"


def test_fr_mu_02_rule_mutants_on_typescript() -> None:
    by_op: dict[str, list[str]] = {}
    for c in generate_ts("src/a.ts", SOURCE, {5, 6, 7}):
        by_op.setdefault(c.operator, []).append(c.replacement.strip())
    assert 'if (subtotal <= 0) throw new RangeError("negative"); // guard' in by_op["boundary"]
    assert any("subtotal / 0.05" in r for r in by_op["arithmetic"])
    assert any("subtotal * 0.06" in r for r in by_op["constant"])
    assert "return undefined;" in by_op["return_value"]
    assert any(r.startswith("if (subtotal < 0) ;") for r in by_op["exception_swallow"])
    assert any("!(subtotal >= FREE)" in r for r in by_op["negation"])


def test_fr_mu_02_type_positions_and_imports_are_never_mutated() -> None:
    text = (
        'import { a } from "./a";\nexport type T = 1 | 2;\nexport const x: Array<number> = [1];\n'
    )
    candidates = generate_ts("src/a.ts", text, {1, 2, 3})
    assert [c.line for c in candidates] == [3]  # only the runtime value `[1]`


def test_fr_mu_07_unreachable_and_constant_folding() -> None:
    text = "export function f(a: number) {\n  return a;\n  a += 1;\n}\nexport const k = 2 * 3;\n"
    reasons = {
        c.replacement.strip(): c.equivalent_reason for c in generate_ts("a.ts", text, {3, 5})
    }
    assert reasons["a -= 1;"] == "unreachable: follows a return/throw in the same block"
    assert reasons["export const k = 2 / 3;"] is None


def test_fr_mu_01_scope_adds_enclosing_condition_and_next_return() -> None:
    text = dedent(
        """\
        export function f(a: number): number {
          if (a > 10) {
            a = a - 1;
          }
          return a * 2;
        }
        """
    )
    assert mutation_scope_ts("a.ts", text, {3}) == {2, 3, 5}


def _pair(base: str | None, head: str | None, path: str = "tests/a.test.ts") -> TestFilePair:
    return TestFilePair(path=path, base_path=path if base else None, base_text=base, head_text=head)


def test_fr_in_01_02_03_on_typescript_test_files() -> None:
    base = 'it("a", () => { expect(f(1)).toBe(2); });\nit("b", () => { expect(f(2)).toBe(3); });\n'
    head = 'it.skip("a", () => { expect(f(1)).toBe(2); });\nit("c", () => { f(2); });\n'
    rules = sorted(f.rule_id for f in compare_test_files([_pair(base, head)], [], None))
    assert rules == ["FR-IN-02", "FR-IN-03"]
    weakened = 'it("a", () => { expect(f(1)).toBe(2); });\nit("b", () => { f(2); });\n'
    assert [f.rule_id for f in compare_test_files([_pair(base, weakened)], [], None)] == [
        "FR-IN-01"
    ]
    assert changed_test_ids([_pair(base, weakened)]) == ["tests/a.test.ts::b"]


def test_fr_in_04_and_06_on_typescript() -> None:
    base = 'it("a", () => { expect(f(1)).toBe(2); expect(() => f(-1)).toThrow(RangeError); });\n'
    head = 'it("a", () => { expect(f(1)).toBeTruthy(); expect(() => f(-1)).toThrow(); });\n'
    (finding,) = compare_test_files([_pair(base, head)], [], None)
    assert finding.rule_id == "FR-IN-04" and "truthiness" in finding.message
    mocked = 'vi.mock("../src/cart/discount");\nit("a", () => { vi.mock("../src/cart/discount"); expect(1).toBe(2); });\n'
    source = ChangedSource("src/cart/discount.ts", "src.cart.discount", frozenset({"percentOff"}))
    rules = [f.rule_id for f in compare_test_files([_pair(None, mocked)], [source], None)]
    assert "FR-IN-06" in rules


@pytest.mark.parametrize(
    ("line", "severity"),
    [
        ("  if (total === 20000 && percent === 25) { return 3000; }", "high"),
        ("  if (currency === 'EUR') { return discount; }", "medium"),
        ("  if (currency === 'EUR') { return 3000; }", "medium"),  # one-word dispatch
        ("  if (currency === 'EUR rate') { return 3000; }", "high"),
        ("  return quantity === 6 ? 5 : quantity;", "high"),
        ("  return ({ 6: 5, 7: 6 } as Record<number, number>)[quantity] ?? quantity;", "high"),
        ("  if ([6, 7].includes(quantity)) { return 5; }", "high"),
        ("  return new Map([[6, 5]]).get(quantity) ?? quantity;", "high"),
    ],
)
def test_fr_in_07_overfit_on_typescript(line: str, severity: str) -> None:
    text = f"export function f(total: number, percent: number, quantity: number, currency: string, discount: number) {{\n{line}\n  return 0;\n}}\n"
    tests = frozenset(
        {
            ("num", "20000"),
            ("num", "25"),
            ("num", "3000"),
            ("num", "6"),
            ("num", "5"),
            ("str", "EUR"),
            ("str", "EUR rate"),
        }
    )
    findings = overfit_findings_ts("src/f.ts", text, {2}, tests)
    assert findings and {f.severity for f in findings} == {severity}


def test_fr_in_07_thresholds_and_unchanged_lines_are_not_overfit() -> None:
    text = "export function f(rate: number) {\n  if (rate > 20) { return 30; }\n  return rate === 25 ? 1 : 2;\n}\n"
    tests = frozenset({("num", "20"), ("num", "30"), ("num", "25")})
    assert overfit_findings_ts("src/f.ts", text, {2}, tests) == []


def test_switch_cases_comparing_test_inputs_are_overfit() -> None:
    text = "export function f(n: number) {\n  switch (n) {\n    case 7:\n      return 13;\n  }\n  return n;\n}\n"
    tests = frozenset({("num", "7"), ("num", "13")})
    (finding,) = overfit_findings_ts("src/f.ts", text, {3, 4}, tests)
    assert finding.severity == "high" and finding.line == 3


def test_positions_of_every_node_in_a_large_file_do_not_crash_the_process() -> None:
    """tree-sitter 0.26.0 segfaults reading ``start_point`` across a large tree (corpus C3:
    three real pull requests crashed the integrity probe). Run in a child process so a crash
    fails this test instead of killing the test run."""
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    script = dedent(
        """\
        from counterproof.probes import tsast
        from counterproof.probes.integrity.overfit_ts import overfit_findings_ts
        text = chr(10).join(f"export const v{i}: number = f({i}) + {i} * 2;" for i in range(1500))
        tree = tsast.parse_tree("big.ts", text)
        rows = [node.start_point.row for node in tsast.walk(tree.root_node)]
        lines = set(range(1, 1501))
        overfit_findings_ts("big.ts", text, lines, frozenset({("num", "7")}))
        print(len(rows))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert int(proc.stdout) > 1500 * 10
