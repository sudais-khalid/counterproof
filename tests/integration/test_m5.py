"""M5 acceptance: P5 claim audit on C2 transcripts and P4 blind oracle on recorded output.

* C2 transcripts with false claims yield REFUTED (R-REF-CLAIM); WEAK-level contradictions
  give WEAK; an honest transcript leaves the verdict alone; P5 runs on a red head too.
* The blind oracle runs on replayed DeepSeek output: honest fixes show no likely bug, its
  tests never touch the user's tree, and ``export-oracle`` copies accepted tests on request.
* FR-BO-07: with a transcript naming the author model, the oracle note says how they differ.
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
    FIXTURE_REPOS,
    TESTS_DIR,
    build_repo,
    git,
    load_expectations,
    repo_state,
)

pytestmark = pytest.mark.integration

LEDGER: dict[str, Any] = load_expectations()["repos"]["py_ledger"]
C2_CASES: dict[str, Any] = LEDGER["c2"]
TRANSCRIPTS = TESTS_DIR / "fixtures" / "transcripts" / "c2"
CASSETTES = TESTS_DIR / "cassettes" / "py_ledger"
SEED = "00" * 16
runner = CliRunner()


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CASSETTE_DIR_ENV, str(CASSETTES))
    monkeypatch.setenv(CASSETTE_MODE_ENV, "replay")


def _build(tmp_path: Path, spec: str) -> Path:
    if spec.startswith("c2/"):
        overlay = FIXTURE_REPOS / "py_ledger" / spec
        return build_repo(
            tmp_path / "repo", task="T1-discount-cap", variant="honest", overlay=overlay
        ).path
    task, variant = spec.split("/")
    return build_repo(tmp_path / "repo", task=task, variant=variant).path


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> RunManifest:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", SEED, *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    manifest = RunManifest.model_validate_json(result.stdout)
    assert result.exit_code == manifest.verdict.exit_code
    return manifest


def _probe(manifest: RunManifest, name: str) -> Any:
    return next(p for p in manifest.probes if p.probe == name)


@pytest.mark.parametrize("case", sorted(C2_CASES))
def test_m5_c2_transcripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    expected = C2_CASES[case]
    repo = _build(tmp_path, expected["repo"])
    before = repo_state(repo)
    manifest = _verify(repo, monkeypatch, "--transcript", str(TRANSCRIPTS / f"{case}.jsonl"))
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["verdict"],
        expected["rule_id"],
    ), manifest.verdict.reasons
    claims = _probe(manifest, "claim_audit")
    assert claims.signals == expected["signals"]
    assert repo_state(repo) == before


def test_m5_false_claim_certificate(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "c2/broken-cap")
    manifest = _verify(repo, monkeypatch, "--transcript", str(TRANSCRIPTS / "masked-green.jsonl"))
    cert = runner.invoke(app, ["report", manifest.run_id, "--format", "md"]).stdout
    assert cert.startswith("## Counterproof certificate: REFUTED  (rule R-REF-CLAIM)")
    assert "| Claim audit  | REFUTED: claimed " in cert
    assert "Claim audit findings:" in cert and "CLAIM_MASKED_EXIT transcript" in cert
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    evidence = json.loads((run_dir / "claims.json").read_text(encoding="utf-8"))
    assert evidence["test_runs"][0]["masked"] is True
    assert evidence["baseline_failing"] == [
        "tests/test_discounts.py::test_discount_below_cap_is_unchanged"
    ]


def test_m5_no_transcript_is_not_a_penalty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _build(tmp_path, "T1-discount-cap/honest")
    manifest = _verify(repo, monkeypatch)
    assert _probe(manifest, "claim_audit").signals == ["CA_NO_TRANSCRIPT"]
    assert (
        manifest.verdict.rule_id
        == LEDGER["tasks"]["T1-discount-cap"]["variants"]["honest"]["provider_none"]["rule_id"]
    )
    assert "agent claims not audited" in manifest.verdict.confidence_note


@pytest.mark.parametrize("task", sorted(LEDGER["tasks"]))
def test_m5_oracle_on_honest_fixes_finds_no_likely_bug(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch, replay: None, task: str
) -> None:
    repo = _build(tmp_path, f"{task}/honest")
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    assert manifest.llm is not None and manifest.llm.errors == []
    oracle = _probe(manifest, "blind_oracle")
    assert oracle.status == "ok" and oracle.metrics["valid"] >= 3
    assert oracle.findings == []
    # Anti-gaming: oracle tests live only in the run directory.
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    record = json.loads((run_dir / "oracle" / "oracle.json").read_text(encoding="utf-8"))
    assert not (repo / record["file"]).exists()
    tree = "\n".join(
        p.read_text(encoding="utf-8") for p in repo.rglob("*.py") if ".git" not in p.parts
    )
    assert all(t["name"] not in tree for t in record["tests"])
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_m5_oracle_likely_bug_on_partial_fix_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: None
) -> None:
    repo = _build(tmp_path, "T3-allocate-remainder/partial")
    manifest = _verify(repo, monkeypatch, "--provider", "deepseek")
    oracle = _probe(manifest, "blind_oracle")
    assert [f.rule_id for f in oracle.findings] == ["BO-LIKELY-BUG"]
    assert manifest.verdict.verdict == "WEAK"
    assert any(r.startswith("[R-WEAK-ORACLE]") for r in manifest.verdict.reasons)
    cert = runner.invoke(app, ["report", manifest.run_id, "--format", "md"]).stdout
    assert "Likely bugs found by the blind oracle (review these):" in cert
    assert f"counterproof export-oracle {manifest.run_id}" in cert
    assert "def test_oracle" not in cert  # the certificate never prints oracle code

    before = repo_state(repo)
    listing = runner.invoke(app, ["export-oracle", manifest.run_id])
    assert listing.exit_code == 0, listing.stderr
    failing = [ln.split()[0] for ln in listing.stdout.splitlines() if "[failing: likely_bug" in ln]
    assert len(failing) == 1
    assert repo_state(repo) == before  # listing writes nothing
    exported = runner.invoke(app, ["export-oracle", manifest.run_id, "--accept", failing[0]])
    assert exported.exit_code == 0, exported.stderr
    (written,) = (repo / "tests").glob("test_counterproof_oracle_*.py")
    assert f"def {failing[0]}(" in written.read_text(encoding="utf-8")
    again = runner.invoke(app, ["export-oracle", manifest.run_id, "--accept", failing[0]])
    assert again.exit_code == 2 and "already exists" in again.stderr
    unknown = runner.invoke(app, ["export-oracle", manifest.run_id, "--accept", "test_nope"])
    assert unknown.exit_code == 2


def test_m5_oracle_model_differs_from_the_transcript_author(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch, replay: None
) -> None:
    repo = _build(tmp_path, "T1-discount-cap/honest")
    manifest = _verify(
        repo,
        monkeypatch,
        "--provider",
        "deepseek",
        "--transcript",
        str(TRANSCRIPTS / "honest-green.jsonl"),
    )
    assert manifest.verdict.rule_id == "R-PROVEN"
    assert _probe(manifest, "claim_audit").signals == ["CA_CONSISTENT"]
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    record = json.loads((run_dir / "oracle" / "oracle.json").read_text(encoding="utf-8"))
    assert record["models"] == ["deepseek-v4-pro"]
    assert "code author claude-sonnet-5-5: different family" in record["model_note"]


def test_m5_fast_profile_has_no_oracle_but_audits_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "c2/broken-cap")
    manifest = _verify(
        repo,
        monkeypatch,
        "--profile",
        "fast",
        "--transcript",
        str(TRANSCRIPTS / "false-green.jsonl"),
    )
    assert "blind_oracle" not in {p.probe for p in manifest.probes}
    assert manifest.verdict.rule_id == "R-REF-CLAIM"
