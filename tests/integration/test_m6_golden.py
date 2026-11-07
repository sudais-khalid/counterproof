"""M6 acceptance: the TypeScript fixture ts_cart (Vitest) and a plain-JS Jest repo.

* ts_cart: every variant gets its recorded ``provider_none`` and ``provider_llm`` verdict
  (seed 00..00; LLM output replayed from tests/cassettes/ts_cart). Gamed variants are
  REFUTED by the integrity rule they were built to trigger; the user's tree is untouched.
* Worktrees reuse the repository's node_modules through a link and never write into it.
* js_jest_mini: the Jest adapter end to end.

Needs ``npm ci`` in tests/fixtures/node; CI sets COUNTERPROOF_REQUIRE_NODE=1 so these
tests cannot be skipped there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.store import repo_hash, runs_root
from counterproof.llm.cache import CASSETTE_DIR_ENV, CASSETTE_MODE_ENV
from counterproof.models import RunManifest
from tests.helpers import (
    FIXTURE_NODE_MODULES,
    TESTS_DIR,
    all_variants,
    build_repo,
    load_expectations,
    repo_state,
    skip_without_node,
)

pytestmark = [pytest.mark.integration, skip_without_node()]

TASKS: dict[str, Any] = load_expectations()["repos"]["ts_cart"]["tasks"]
CASSETTES = TESTS_DIR / "cassettes" / "ts_cart"
SEED = "00" * 16
runner = CliRunner()


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> RunManifest:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", SEED, *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    manifest = RunManifest.model_validate_json(result.stdout)
    assert result.exit_code == manifest.verdict.exit_code
    return manifest


def _shared_caches() -> set[str]:
    return {p.name for p in FIXTURE_NODE_MODULES.iterdir() if p.name.startswith(".vit")}


@pytest.mark.parametrize(("task", "variant"), all_variants("ts_cart"))
def test_m6_ts_cart_provider_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str, variant: str
) -> None:
    repo = build_repo(tmp_path / "repo", "ts_cart", task, variant).path
    before = repo_state(repo)
    caches = _shared_caches()
    manifest = _verify(repo, monkeypatch)
    expected = TASKS[task]["variants"][variant]
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["provider_none"]["verdict"],
        expected["provider_none"]["rule_id"],
    ), manifest.verdict.reasons
    if "integrity_rule" in expected:
        integrity = next(p for p in manifest.probes if p.probe == "integrity")
        assert expected["integrity_rule"] in {f.rule_id for f in integrity.findings}
    assert repo_state(repo) == before
    assert _shared_caches() == caches  # nothing written into the linked node_modules


@pytest.mark.parametrize(("task", "variant"), all_variants("ts_cart"))
def test_m6_ts_cart_replayed_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str, variant: str
) -> None:
    monkeypatch.setenv(CASSETTE_DIR_ENV, str(CASSETTES))
    monkeypatch.setenv(CASSETTE_MODE_ENV, "replay")
    repo = build_repo(tmp_path / "repo", "ts_cart", task, variant).path
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    assert manifest.llm is not None and manifest.llm.errors == [], "missing cassette"
    expected = TASKS[task]["variants"][variant]["provider_llm"]
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["verdict"],
        expected["rule_id"],
    ), manifest.verdict.reasons


def test_m6_fast_check_fuzz_overrules_a_wrong_equivalence_judgement(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-MU-09 on TypeScript: the judge calls `total % parts` equivalent; fast-check finds
    a negative total where JavaScript's `%` keeps the sign, so the survivor stays."""
    monkeypatch.setenv(CASSETTE_DIR_ENV, str(CASSETTES))
    monkeypatch.setenv(CASSETTE_MODE_ENV, "replay")
    repo = build_repo(tmp_path / "repo", "ts_cart", "T2-split-evenly", "honest").path
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    mutants = json.loads((run_dir / "mutants.json").read_text(encoding="utf-8"))["mutants"]
    modulo = next(m for m in mutants if "total % parts" in m["replacement"])
    assert modulo["status"] == "survived"
    assert modulo["equivalence_note"] == "judge: equivalent (0.95); fuzz: differs"
    head_src = repo / "src" / "cart"
    assert not list(head_src.glob("*.cp*"))  # fuzz files never reach the user tree


def test_m6_certificate_for_typescript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = build_repo(tmp_path / "repo", "ts_cart", "T1-discount-cap", "honest").path
    manifest = _verify(repo, monkeypatch)
    cert = runner.invoke(app, ["report", manifest.run_id, "--format", "md"]).stdout
    assert cert.startswith("## Counterproof certificate: PROVEN  (rule R-PROVEN)")
    assert "| Red-green    | PASS: 2 of 3 test(s) fail on the old code" in cert
    assert "`if (cap !== undefined && discount > cap) {` -> " in cert
    assert "| Blind oracle | not run (LLM disabled) |" in cert


@pytest.mark.parametrize(
    ("variant", "verdict", "rule_id"),
    [("honest", "PROVEN", "R-PROVEN"), ("hardcoded", "WEAK", "R-WEAK-MEDIUM")],
)
def test_m6_jest_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str, verdict: str, rule_id: str
) -> None:
    """FR-AD-03. The hard-coded special case returns 0, a trivial value, so FR-IN-07 is
    medium (review) rather than high: only the compared input matches the tests."""
    repo = build_repo(tmp_path / "repo", "js_jest_mini", "T1-reserve-all", variant).path
    before = repo_state(repo)
    manifest = _verify(repo, monkeypatch)
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (verdict, rule_id)
    probes = {p.probe: p for p in manifest.probes}
    assert probes["red_green"].signals == ["RG_STRONG"]
    if variant == "hardcoded":
        assert [(f.rule_id, f.severity) for f in probes["integrity"].findings] == [
            ("FR-IN-07", "medium")
        ]
    assert repo_state(repo) == before
