"""M4 acceptance with recorded DeepSeek output (cassettes, offline) and the budget hard stop.

* C1 replay: every variant gets its recorded ``provider_llm`` verdict; partial fixes are
  UNPROVEN or WEAK; no cassette is missing (prompts are deterministic).
* BudgetGuard: an exhausted budget skips all LLM work, the run still completes with rule
  mutants, and the certificate says what was skipped. No network is reachable in that test.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.eval.leakage import export, score
from counterproof.evidence.store import repo_hash, runs_root
from counterproof.llm.cache import CASSETTE_DIR_ENV, CASSETTE_MODE_ENV
from counterproof.models import RunManifest
from tests.helpers import TESTS_DIR, all_variants, build_repo, load_expectations, repo_state

pytestmark = pytest.mark.integration

TASKS: dict[str, Any] = load_expectations()["repos"]["py_ledger"]["tasks"]
CASSETTES = TESTS_DIR / "cassettes" / "py_ledger"
SEED = "00" * 16
runner = CliRunner()


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CASSETTE_DIR_ENV, str(CASSETTES))
    monkeypatch.setenv(CASSETTE_MODE_ENV, "replay")


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> RunManifest:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", SEED, *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    return RunManifest.model_validate_json(result.stdout)


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_m4_c1_replayed_llm_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: None, task: str, variant: str
) -> None:
    repo = build_repo(tmp_path / "repo", task=task, variant=variant).path
    before = repo_state(repo)
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    assert manifest.llm is not None and manifest.llm.provider == "deepseek"
    assert manifest.llm.errors == [], "a cassette is missing or a recorded reply is invalid"
    expected = TASKS[task]["variants"][variant]["provider_llm"]
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["verdict"],
        expected["rule_id"],
    ), manifest.verdict.reasons
    if variant == "partial" and TASKS[task].get("tier") != "extended":
        # M4 acceptance, for the original five tasks it was written for. Extended-tier partial
        # fixes are pinned by their golden verdict above, including the two Counterproof misses.
        assert manifest.verdict.verdict in {"UNPROVEN", "WEAK"}
    assert repo_state(repo) == before


def test_m4_budget_hard_stop_skips_llm_work_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    # An exhausted time budget stops every call before a request is built; the endpoint is a
    # closed local port, so even a bug here could not reach a real provider.
    (repo / "counterproof.toml").write_text(
        '[llm]\nprovider = "deepseek"\nbudget_seconds = 0.000001\n'
        'base_url = "http://127.0.0.1:9/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("COUNTERPROOF_LLM_CASSETTES", str(tmp_path / "empty-cassettes"))
    monkeypatch.setenv("COUNTERPROOF_LLM_MODE", "record")
    manifest = _verify(repo, monkeypatch, "--config-from", "worktree")
    usage = manifest.llm
    assert usage is not None and usage.tokens == 0 and usage.usd == 0
    assert usage.skipped and all("time budget exhausted" in s for s in usage.skipped)
    assert manifest.verdict.rule_id == "R-WEAK-SURVIVOR"  # same as rule mutants only
    assert "LLM budget exhausted" in manifest.verdict.confidence_note
    cert = runner.invoke(app, ["report", manifest.run_id, "--format", "md"]).stdout
    assert "Budget skipped: mutant_forge" in cert


def test_m4_certificate_states_which_provider_saw_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: None
) -> None:
    repo = build_repo(tmp_path / "repo", task="T3-allocate-remainder", variant="partial").path
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    cert = runner.invoke(app, ["report", manifest.run_id, "--format", "md"]).stdout
    assert "Changed code was sent to deepseek" in cert
    no_llm = _verify(repo, monkeypatch)
    cert = runner.invoke(app, ["report", no_llm.run_id, "--format", "md"]).stdout
    assert "No code was sent to any provider" in cert


def test_m4_leakage_export_and_score(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch, replay: None
) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    count = export([run_dir], tmp_path / "sheet")
    survived = int(next(p for p in manifest.probes if p.probe == "mutants").metrics["survived"])
    assert count == survived
    sheet = tmp_path / "sheet" / "survivors.csv"
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    assert len(rows) == count
    assert (
        (tmp_path / "sheet" / "survivors.md").read_text(encoding="utf-8").startswith("# Surviving")
    )
    if rows:
        rows[0]["human_label"] = "equivalent"
        with sheet.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = score(sheet)
        assert (result.labelled, result.equivalent) == (1, 1)
        assert result.unlabelled == count - 1
