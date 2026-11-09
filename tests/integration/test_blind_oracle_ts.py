"""P4 blind oracle on real Vitest and Jest (FR-BO-02 to FR-BO-05): model-written tests run
three times on head, failing ones once on base, then triage. Fake providers; real Node."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from counterproof import git
from counterproof.adapters.jest_adapter import JestAdapter
from counterproof.adapters.vitest_adapter import VitestAdapter
from counterproof.change.resolver import resolve_change
from counterproof.change.task import TaskText
from counterproof.llm.budget import BudgetGuard
from counterproof.llm.cache import LLMCache
from counterproof.llm.roles import LLMSession
from counterproof.probes.blind_oracle.interface_ts import (
    changed_function_sources_ts,
    extract_interface_ts,
)
from counterproof.probes.blind_oracle.probe import BO_LIKELY_BUG, LIKELY_BUG_RULE, run_blind_oracle
from counterproof.workspace.links import unlink_all
from counterproof.workspace.manager import Workspace
from tests.helpers import build_repo, skip_without_node
from tests.helpers import git as run_git
from tests.unit.test_llm import FakeProvider

pytestmark = [pytest.mark.integration, skip_without_node()]


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


def _worktrees(
    tmp_path: Path, name: str, task: str, variant: str
) -> tuple[Path, Workspace, Workspace]:
    """The built repo is head; a second checkout of the base commit is base."""
    repo = build_repo(tmp_path / "repo", name=name, task=task, variant=variant)
    base_dir = tmp_path / "base"
    run_git(repo.path, "worktree", "add", "-q", "--detach", str(base_dir), repo.base_sha)
    nm = repo.path / "node_modules"
    if nm.exists():
        from counterproof.workspace.links import link_dir  # noqa: PLC0415

        target = base_dir / "node_modules"
        if not target.exists() and not target.is_symlink():  # on POSIX git tracks the link
            link_dir(nm.resolve(), target)
    return (
        repo.path,
        Workspace("head", repo.path, "HEAD"),
        Workspace("base", base_dir, repo.base_sha),
    )


def _writer(imports: list[str], tests: dict[str, str]) -> str:
    return json.dumps(
        {
            "imports": imports,
            "tests": [{"name": n, "intent": n, "code": c} for n, c in tests.items()],
        }
    )


def test_fr_bo_ts_oracle_catches_the_partial_split_on_vitest(tmp_path: Path) -> None:
    """ts_cart T2 partial gives every leftover cent to the first share. An oracle test written
    from the task (two leftover cents) fails on head, passes nowhere on base (the bug is old
    too) and a confident triage turns it into BO-LIKELY-BUG: a WEAK cap, never a refutation."""
    repo, head, base = _worktrees(tmp_path, "ts_cart", "T2-split-evenly", "partial")
    change = resolve_change(
        repo,
        base_sha=base.sha,
        head_ref="HEAD",
        head_sha=run_git(repo, "rev-parse", "HEAD").strip(),
        task=TaskText(
            "splitEvenly must hand leftover cents one at a time to the earliest parts.", "cli"
        ),
    )
    read_head = partial(git.show_file, change.repo_root, change.head_snapshot_sha)
    read_base = partial(git.show_file, change.repo_root, change.base_sha)
    interface = extract_interface_ts(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=["tests/money.test.ts"],
        framework="vitest",
    )
    writer = _writer(
        ['import { splitEvenly } from "../src/cart/money";'],
        {
            "one leftover cent": 'test("one leftover cent", () => {\n'
            "  expect(splitEvenly(1000, 3)).toEqual([334, 333, 333]);\n});",
            "two leftover cents": 'test("two leftover cents", () => {\n'
            "  expect(splitEvenly(1001, 3)).toEqual([334, 334, 333]);\n});",
            "even split": 'test("even split", () => {\n'
            "  expect(splitEvenly(900, 3)).toEqual([300, 300, 300]);\n});",
        },
    )
    triage = json.dumps(
        {"label": "likely_bug", "confidence": 0.9, "explanation": "1001/3 must be [334,334,333]"}
    )
    session, provider = _session([writer, triage])
    outcome = run_blind_oracle(
        session=session,
        models=["writer"],
        model_note="n",
        judge_model="judge",
        k=6,
        task_text=change.task_text,
        interface=interface,
        functions=changed_function_sources_ts(change, read_head),
        adapter=VitestAdapter(repo),
        head=head,
        base=base,
        run_id="r1",
        junit_path=lambda n: tmp_path / f"{n}.xml",
        timeout_s=120,
    )
    try:
        tests = {t["name"]: t for t in outcome.record["tests"]}
        assert outcome.record["file"] == "tests/counterproof_oracle.test.ts"
        assert tests["test_oracle_even_split"]["status"] == "agree"
        assert tests["test_oracle_one_leftover_cent"]["status"] == "agree"
        two = tests["test_oracle_two_leftover_cents"]
        assert two["status"] == "failing" and two["head_outcomes"] == ["fail"] * 3
        assert two["base_outcome"] == "fail"  # base never split leftovers at all
        assert outcome.result.signals == ["BO_FAILURES", BO_LIKELY_BUG]
        assert [f.rule_id for f in outcome.result.findings] == [LIKELY_BUG_RULE]
        assert outcome.result.findings[0].evidence_refs[-1] == "oracle/test_oracle.ts"
        # The triage judge saw the TS prompt with the head source of splitEvenly only.
        judge = provider.requests[1]
        assert "```typescript" in judge.user and "shares[0] +=" in judge.user
        # Failure text is portable: no absolute path of this machine reaches the prompt.
        assert (
            str(tmp_path) not in judge.user and str(tmp_path).replace("\\", "/") not in judge.user
        )
        assert not (repo / outcome.record["file"]).exists()  # removed after the runs
    finally:
        unlink_all(head.root)
        unlink_all(base.root)


def test_fr_bo_ts_commonjs_oracle_agrees_on_jest(tmp_path: Path) -> None:
    repo, head, base = _worktrees(tmp_path, "js_jest_mini", "T1-reserve-all", "honest")
    change = resolve_change(
        repo,
        base_sha=base.sha,
        head_ref="HEAD",
        head_sha=run_git(repo, "rev-parse", "HEAD").strip(),
        task=TaskText("A reservation may use the whole stock.", "cli"),
    )
    read_head = partial(git.show_file, change.repo_root, change.head_snapshot_sha)
    read_base = partial(git.show_file, change.repo_root, change.base_sha)
    interface = extract_interface_ts(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=["test/stock.test.js"],
        framework="jest",
    )
    assert interface.file_suffix == ".test.js" and "../src/stock" in interface.public_names
    writer = _writer(
        ['const { reserve } = require("../src/stock");'],
        {
            "whole stock": 'test("whole stock", () => {\n  expect(reserve(5, 5)).toBe(0);\n});',
            "too much": 'test("too much", () => {\n'
            '  expect(() => reserve(5, 6)).toThrow("not enough stock");\n});',
        },
    )
    session, _ = _session([writer])
    outcome = run_blind_oracle(
        session=session,
        models=["writer"],
        model_note="n",
        judge_model="judge",
        k=6,
        task_text=change.task_text,
        interface=interface,
        functions=changed_function_sources_ts(change, read_head),
        adapter=JestAdapter(repo),
        head=head,
        base=base,
        run_id="r2",
        junit_path=lambda n: tmp_path / f"{n}.xml",
        timeout_s=120,
    )
    try:
        assert outcome.record["file"] == "test/counterproof_oracle.test.js"
        assert {t["name"]: t["status"] for t in outcome.record["tests"]} == {
            "test_oracle_whole_stock": "agree",
            "test_oracle_too_much": "agree",
        }
        assert outcome.result.signals == ["BO_AGREE"] and not outcome.result.findings
    finally:
        unlink_all(head.root)
        unlink_all(base.root)
