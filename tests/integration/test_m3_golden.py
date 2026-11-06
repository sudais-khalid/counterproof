"""M3 acceptance: first end-to-end verdicts on C1 Python with provider = none.

* Every variant gets its recorded ``provider_none`` verdict and rule id (fixed seed).
* A full run on the fixtures stays inside the 5 minute budget (PRD section 20).
* Same inputs and seed give the same verdict, probes and mutants (PRD section 19).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.store import repo_hash, runs_root
from counterproof.models import RunManifest
from tests.helpers import all_variants, build_repo, load_expectations, repo_state

pytestmark = pytest.mark.integration

TASKS: dict[str, Any] = load_expectations()["repos"]["py_ledger"]["tasks"]
SEED = "00" * 16
BUDGET_S = 300.0
runner = CliRunner()


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> tuple[int, RunManifest]:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", SEED, *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    return result.exit_code, RunManifest.model_validate_json(result.stdout)


def _run_dir(cache: Path, repo: Path, manifest: RunManifest) -> Path:
    return runs_root(cache) / repo_hash(repo) / manifest.run_id


@pytest.mark.parametrize(("task", "variant"), all_variants())
def test_m3_c1_provider_none_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str, variant: str
) -> None:
    repo = build_repo(tmp_path / "repo", task=task, variant=variant).path
    before = repo_state(repo)
    code, manifest = _verify(repo, monkeypatch)
    expected = TASKS[task]["variants"][variant]["provider_none"]
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["verdict"],
        expected["rule_id"],
    ), manifest.verdict.reasons
    assert code == manifest.verdict.exit_code
    assert manifest.duration_s < BUDGET_S
    assert repo_state(repo) == before


def _comparable(manifest: RunManifest, run_dir: Path) -> dict[str, Any]:
    """Everything that must be identical between two runs with the same seed."""
    probes = [
        {
            "probe": p.probe,
            "status": p.status,
            "signals": p.signals,
            "findings": [f.model_dump() for f in p.findings],
            "metrics": p.metrics,
        }
        for p in manifest.probes
    ]
    mutants = json.loads((run_dir / "mutants.json").read_text(encoding="utf-8"))
    return {
        "verdict": manifest.verdict.model_dump(),
        "seed_commitment": manifest.seed_commitment,
        "config_sha256": manifest.config_sha256,
        "change": manifest.change.model_dump(mode="json"),
        "probes": probes,
        "mutants": mutants,
    }


def test_determinism_same_seed_same_result(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo", task="T3-allocate-remainder", variant="honest").path
    _, first = _verify(repo, monkeypatch)
    _, second = _verify(repo, monkeypatch)
    assert first.run_id != second.run_id
    assert _comparable(first, _run_dir(isolated_cache, repo, first)) == _comparable(
        second, _run_dir(isolated_cache, repo, second)
    )


NO_ASSERT_TEST = """from decimal import Decimal

from ledger.discounts import percent_discount
from ledger.money import Money


def test_discount_runs():
    percent_discount(Money.of("80.00"), Decimal("15"))
"""


def test_test_only_change_whose_tests_kill_nothing_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 8: new tests that kill no fault in the code they cover are UNPROVEN."""
    repo = build_repo(tmp_path / "repo").path
    (repo / "tests" / "test_smoke.py").write_text(NO_ASSERT_TEST, encoding="utf-8")
    code, manifest = _verify(repo, monkeypatch, "--task", "Add a smoke test")
    assert manifest.change.intent == "test_only"
    probes = {p.probe: p for p in manifest.probes}
    assert probes["red_green"].signals == ["RG_NOT_APPLICABLE"]
    # Without assertions only mutants that crash the code are killed (FR-MU-10 counts errors).
    assert probes["mutants"].metrics["valid"] >= 3
    assert probes["mutants"].metrics["kill_ratio"] < 0.5
    assert manifest.verdict.rule_id == "R-UNP-WEAKTESTS"
    assert code == 20


def test_certificate_report_explain_and_fail_on(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "md", "--seed", SEED])
    assert result.exit_code == 10
    cert = result.stdout
    assert cert.startswith("## Counterproof certificate: WEAK  (rule R-WEAK-SURVIVOR)")
    assert "| Red-green    | PASS: 1 of 1 test(s) fail on the old code" in cert
    assert "| Integrity    | OK (0 findings) |" in cert
    # Soft diversity cap (approved in M4): 8 of 11 candidates run; still the untested 20% gap.
    assert "| Mutants      | 5/8 killed (0.62). 3 survivor(s) |" in cert
    assert "`if cap is not None and rate >= 20 and discount > cap:`" in cert
    assert "Missing test: Add a test where the two sides of the comparison" in cert
    assert "LLM disabled: rule mutants only" in cert

    run_id = next(iter((runs_root(isolated_cache) / repo_hash(repo)).iterdir())).name
    report = runner.invoke(app, ["report", run_id, "--format", "md"])
    assert report.exit_code == 0
    assert report.stdout == cert
    as_json = runner.invoke(app, ["report", run_id, "--format", "json"])
    assert RunManifest.model_validate_json(as_json.stdout).run_id == run_id

    explained = runner.invoke(app, ["explain", run_id, "r002"])
    assert explained.exit_code == 0
    assert "+    if cap is not None and rate >= 20 and discount > cap:" in explained.stdout
    assert "Tests run: " in explained.stdout
    assert runner.invoke(app, ["explain", run_id, "r999"]).exit_code == 2
    assert runner.invoke(app, ["report", "20990101T000000Z-00000000"]).exit_code == 2

    lenient = runner.invoke(app, ["verify", "--seed", SEED, "--fail-on", "unproven"])
    assert lenient.exit_code == 0
    strict = runner.invoke(app, ["verify", "--seed", SEED, "--fail-on", "weak"])
    assert strict.exit_code == 10
    assert strict.stdout.startswith("Counterproof verdict: WEAK (rule R-WEAK-SURVIVOR)")


def test_mutants_never_written_to_the_user_tree(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-gaming: mutants live only in the run directory, never in the repository."""
    repo = build_repo(tmp_path / "repo", task="T3-allocate-remainder", variant="honest").path
    _, manifest = _verify(repo, monkeypatch)
    run_dir = _run_dir(isolated_cache, repo, manifest)
    mutants = json.loads((run_dir / "mutants.json").read_text(encoding="utf-8"))["mutants"]
    assert mutants
    tree = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in repo.rglob("*.py")
        if ".git" not in p.parts
    )
    for m in mutants:
        assert m["replacement"] not in tree
    assert git_worktrees(repo) == 1


def git_worktrees(repo: Path) -> int:
    from tests.helpers import git  # noqa: PLC0415

    return git(repo, "worktree", "list", "--porcelain").count("worktree ")
