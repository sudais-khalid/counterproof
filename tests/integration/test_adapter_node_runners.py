"""VitestAdapter and JestAdapter against real Node (FR-AD-02, FR-AD-03, FR-AD-04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from counterproof.adapters.jest_adapter import JestAdapter
from counterproof.adapters.vitest_adapter import VitestAdapter
from counterproof.workspace.links import unlink_all
from counterproof.workspace.manager import Workspace
from tests.helpers import build_repo, skip_without_node

pytestmark = [pytest.mark.integration, skip_without_node()]


def _ws(tmp_path: Path, name: str) -> tuple[Path, Workspace]:
    repo = build_repo(tmp_path / "repo", name).path
    return repo, Workspace("head", repo, "HEAD")


def test_vitest_lists_runs_subsets_and_classifies(tmp_path: Path) -> None:
    repo, ws = _ws(tmp_path, "ts_cart")
    adapter = VitestAdapter(repo)
    assert adapter.prepare(ws).ok
    tests = adapter.list_tests(ws)
    ids = [t.nodeid for t in tests]
    assert "tests/money.test.ts::splitEvenly > splits evenly divisible totals" in ids
    assert len(ids) == 20
    wanted = [
        "tests/money.test.ts::splitEvenly > rejects zero parts",
        "tests/tax.test.ts::taxOnNet > adds tax on top of a net price",
    ]
    result = adapter.run(ws, wanted, timeout_s=120, fail_fast=False, junit_path=tmp_path / "j.xml")
    assert result.tests == dict.fromkeys(wanted, "pass")

    (repo / "tests" / "money.test.ts").write_text(
        'import { it, expect } from "vitest";\nimport { sum } from "../src/cart/money";\n'
        'it("fails", () => { expect(sum([1])).toBe(2); });\n'
        'it("throws", () => { (sum as unknown as () => void)(); });\n',
        encoding="utf-8",
    )
    (repo / "tests" / "tax.test.ts").write_text("it('x', () => { const = 1 });\n", encoding="utf-8")
    ran = adapter.run(
        ws,
        ["tests/money.test.ts::fails", "tests/money.test.ts::throws", "tests/tax.test.ts::x"],
        timeout_s=120,
        fail_fast=False,
        junit_path=tmp_path / "k.xml",
    )
    assert ran.tests["tests/money.test.ts::fails"] == "fail"
    assert ran.failures["tests/money.test.ts::fails"].kind == "ASSERTION"
    assert ran.failures["tests/money.test.ts::throws"].kind == "EXCEPTION_BEHAVIOURAL"
    assert ran.failures["tests/tax.test.ts::x"].kind == "COLLECTION"
    unlink_all(ws.root)


def test_vitest_related_tests_come_from_the_import_graph(tmp_path: Path) -> None:
    repo, ws = _ws(tmp_path, "ts_cart")
    adapter = VitestAdapter(repo)
    tests = adapter.list_tests(ws)
    related = {t.file for t in adapter.related_tests(["src/cart/money.ts"], tests)}
    # discount, tax and rounding import money.ts (assertCents) too
    assert related == {
        "tests/money.test.ts",
        "tests/discount.test.ts",
        "tests/tax.test.ts",
        "tests/rounding.test.ts",
    }
    assert {t.file for t in adapter.related_tests(["src/cart/offers.ts"], tests)} == {
        "tests/offers.test.ts"
    }


def test_jest_lists_statically_runs_and_finds_related(tmp_path: Path) -> None:
    repo, ws = _ws(tmp_path, "js_jest_mini")
    adapter = JestAdapter(repo)
    assert adapter.prepare(ws).ok
    tests = adapter.list_tests(ws)
    assert [t.nodeid for t in tests] == [
        "test/stock.test.js::reserve > takes units from stock",
        "test/stock.test.js::reserve > refuses more than the stock",
        "test/stock.test.js::reserve > rejects zero units",
    ]
    assert adapter.related_tests(["src/stock.js"], tests) == tests
    ids = [t.nodeid for t in tests]
    result = adapter.run(ws, ids[:2], timeout_s=120, fail_fast=False, junit_path=tmp_path / "j.xml")
    assert result.tests == dict.fromkeys(ids[:2], "pass")
    unlink_all(ws.root)
