"""End-to-end CLI tests for the M0 skeleton (`verify`, `gc`, stubs)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.hashing import sha256_bytes
from counterproof.evidence.seed import seed_commitment
from counterproof.evidence.store import EvidenceStore, repo_hash, runs_root
from counterproof.models import VERDICT_EXIT_CODES, RunManifest
from tests.helpers import git, tree_hash

pytestmark = pytest.mark.integration

runner = CliRunner()


def _verify(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, ["verify", *args])
    return result.exit_code, result.stdout, result.stderr


HEADER = re.compile(r"^Counterproof verdict: ([A-Z]+) \(rule ([\w-]+)\)$", re.MULTILINE)


def _completed(code: int, out: str) -> bool:
    """verify ran to a verdict and exited with that verdict's own exit code."""
    if out.lstrip().startswith("{"):
        return code == RunManifest.model_validate_json(out).verdict.exit_code
    match = HEADER.search(out)
    return match is not None and code == VERDICT_EXIT_CODES[match.group(1)]  # type: ignore[index]


def test_m0_verify_creates_run_dir_and_empty_manifest(in_repo: Path, isolated_cache: Path) -> None:
    code, out, err = _verify("--format", "json", "--task", "fix rounding")
    assert _completed(code, out), err

    manifest = RunManifest.model_validate_json(out)
    # M5 (approved): P5 runs first and P4 last; without a transcript or LLM both are skipped.
    assert [p.probe for p in manifest.probes] == [
        "claim_audit",
        "integrity",
        "red_green",
        "mutants",
        "blind_oracle",
    ]
    assert manifest.change.repo_root == in_repo.resolve()
    assert manifest.change.base_sha == git(in_repo, "rev-parse", "HEAD").strip()
    assert manifest.change.head_ref == "WORKTREE"
    assert manifest.change.task_text == "fix rounding"

    run_dir = runs_root(isolated_cache) / repo_hash(in_repo) / manifest.run_id
    assert run_dir.is_dir()
    assert not run_dir.resolve().is_relative_to(in_repo.resolve())
    store = EvidenceStore.open(run_dir)
    assert store.load_manifest() == manifest
    # M1 adds selection, baseline and junit evidence on top of the M0 artefacts.
    expected = {
        "change.json",
        "config.json",
        "run.log",
        "selection.json",
        "baseline.json",
        "baseline-runs.json",
        "junit/baseline-1.xml",
        "junit/baseline-2.xml",
        "junit/baseline-3.xml",
    }
    assert expected <= set(manifest.artefacts)
    assert store.verify() == []


def test_m0_seed_is_committed_not_published(in_repo: Path, isolated_cache: Path) -> None:
    code, out, _ = _verify("--format", "json")
    assert _completed(code, out)
    manifest = RunManifest.model_validate_json(out)
    run_dir = runs_root(isolated_cache) / repo_hash(in_repo) / manifest.run_id
    seed_hex = (run_dir / "seed").read_text(encoding="utf-8")
    assert manifest.seed_commitment == seed_commitment(bytes.fromhex(seed_hex))
    assert "seed" not in manifest.artefacts
    assert seed_hex not in out
    assert seed_hex not in (run_dir / "manifest.json").read_text(encoding="utf-8")


def test_explicit_seed_is_reproducible_and_fresh_seeds_differ(in_repo: Path) -> None:
    seed = "00112233445566778899aabbccddeeff"
    first = RunManifest.model_validate_json(_verify("--format", "json", "--seed", seed)[1])
    second = RunManifest.model_validate_json(_verify("--format", "json", "--seed", seed)[1])
    assert first.run_id != second.run_id
    assert first.seed_commitment == second.seed_commitment == seed_commitment(bytes.fromhex(seed))
    third = RunManifest.model_validate_json(_verify("--format", "json")[1])
    fourth = RunManifest.model_validate_json(_verify("--format", "json")[1])
    assert third.seed_commitment != fourth.seed_commitment


def test_fr_ws_01_verify_leaves_user_working_tree_unchanged(in_repo: Path) -> None:
    index = in_repo / ".git" / "index"
    before_tree, before_index = tree_hash(in_repo), sha256_bytes(index.read_bytes())
    before_status = git(in_repo, "status", "--porcelain=v1", "--untracked-files=all")

    assert _completed(*_verify("--format", "json")[:2])
    assert _completed(*_verify("--head", "HEAD", "--profile", "fast")[:2])

    assert tree_hash(in_repo) == before_tree
    assert sha256_bytes(index.read_bytes()) == before_index
    assert git(in_repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status


def test_cli_overrides_are_recorded_in_config(in_repo: Path, isolated_cache: Path) -> None:
    code, out, _ = _verify(
        "--format", "json", "--profile", "fast", "--workers", "3", "--no-short-circuit"
    )
    assert _completed(code, out)
    manifest = RunManifest.model_validate_json(out)
    run_dir = runs_root(isolated_cache) / repo_hash(in_repo) / manifest.run_id
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert cfg["run"]["profile"] == "fast"
    assert cfg["run"]["workers"] == 3
    assert cfg["run"]["short_circuit"] is False
    assert cfg["mutants"]["budget"] == 4


def test_repo_config_file_is_loaded(in_repo: Path, isolated_cache: Path) -> None:
    (in_repo / "counterproof.toml").write_text("[mutants]\nbudget = 5\n", encoding="utf-8")
    manifest = RunManifest.model_validate_json(
        _verify("--format", "json", "--config-from", "worktree")[1]
    )
    run_dir = runs_root(isolated_cache) / repo_hash(in_repo) / manifest.run_id
    assert (
        json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["mutants"]["budget"] == 5
    )


def test_invalid_repo_config_is_a_usage_error(in_repo: Path) -> None:
    (in_repo / "counterproof.toml").write_text("[mutants]\nbudgte = 5\n", encoding="utf-8")
    code, _, err = _verify("--config-from", "worktree")
    assert code == 2
    assert "budgte" in err


def test_head_commit_ref_is_resolved(in_repo: Path) -> None:
    manifest = RunManifest.model_validate_json(_verify("--format", "json", "--head", "HEAD")[1])
    sha = git(in_repo, "rev-parse", "HEAD").strip()
    assert manifest.change.head_ref == sha
    assert manifest.change.head_snapshot_sha == sha


def test_text_output_names_run_and_verdict(in_repo: Path) -> None:
    code, out, _ = _verify()
    assert out.startswith("Counterproof verdict: ")
    assert _completed(code, out)
    assert "Run directory:" in out


def test_out_writes_to_file(in_repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    code, out, _ = _verify("--format", "json", "--out", str(target))
    assert out == ""
    assert _completed(code, target.read_text(encoding="utf-8"))


def test_task_file_is_read(in_repo: Path, tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("Discount must cap at 50%.\n", encoding="utf-8")
    manifest = RunManifest.model_validate_json(
        _verify("--format", "json", "--task-file", str(task))[1]
    )
    assert manifest.change.task_text == "Discount must cap at 50%.\n"


def test_intent_override_is_recorded_as_user(in_repo: Path) -> None:
    manifest = RunManifest.model_validate_json(_verify("--format", "json", "--intent", "bugfix")[1])
    assert (manifest.change.intent, manifest.change.intent_source) == ("bugfix", "user")


@pytest.mark.parametrize(
    "args",
    [
        ["--base", "no-such-ref"],
        ["--head", "--not-a-ref"],
        ["--task", "a", "--task-file", "b.md"],
        ["--seed", "zz"],
        ["--transcript", "missing.jsonl"],
        ["--task-file", "missing.md"],
        ["--profile", "turbo"],
        ["--workers", "0"],
    ],
)
def test_usage_errors_exit_2(in_repo: Path, args: list[str]) -> None:
    code, _, err = _verify(*args)
    assert code == 2, err


def test_verify_outside_git_repo_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    code, _, err = _verify()
    assert code == 2
    assert "not inside a git repository" in err


def test_error_messages_are_never_hard_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (macOS CI): Rich wrapped long errors at 80 columns, splitting the message
    across lines and breaking anyone who greps for it."""
    plain = tmp_path / ("a-rather-long-directory-name-" * 3) / "plain"
    plain.mkdir(parents=True)
    assert len(str(plain)) > 100
    monkeypatch.chdir(plain)
    code, _, err = _verify()
    assert code == 2
    assert err.count("\n") == 1, repr(err)
    assert err.rstrip("\n").endswith("is not inside a git repository")


@pytest.mark.parametrize(
    "args",
    [
        ["eval", "corpus"],  # M7 (approved): html/sarif, hook claude-stop and mcp are real now
    ],
)
def test_stub_commands_report_not_implemented(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented yet" in result.stderr


def test_gc_command_removes_nothing_when_fresh(in_repo: Path) -> None:
    assert _completed(*_verify()[:2])
    result = runner.invoke(app, ["gc", "--older-than", "7d"])
    assert result.exit_code == 0
    assert "removed 0 run(s)" in result.stdout
    assert runner.invoke(app, ["gc", "--older-than", "soon"]).exit_code == 2


def test_python_dash_m_entry_point() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "counterproof", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("counterproof ")


def _config_json(cache: Path, repo: Path, manifest: RunManifest) -> dict[str, Any]:
    run_dir = runs_root(cache) / repo_hash(repo) / manifest.run_id
    data: dict[str, Any] = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    return data


def test_config_comes_from_base_commit_by_default(in_repo: Path, isolated_cache: Path) -> None:
    """PRD 15.3: the committed (base) config applies; an uncommitted edit cannot loosen it."""
    (in_repo / "counterproof.toml").write_text("[mutants]\nbudget = 5\n", encoding="utf-8")
    git(in_repo, "add", "counterproof.toml")
    git(in_repo, "commit", "-q", "-m", "add config")
    (in_repo / "counterproof.toml").write_text("[mutants]\nbudget = 1\n", encoding="utf-8")
    manifest = RunManifest.model_validate_json(_verify("--format", "json")[1])
    assert _config_json(isolated_cache, in_repo, manifest)["mutants"]["budget"] == 5
    assert "self_config_change" in manifest.change.flags


def test_uncommitted_invalid_config_is_ignored_by_default(in_repo: Path) -> None:
    (in_repo / "counterproof.toml").write_text("[mutants]\nbudgte = 5\n", encoding="utf-8")
    code, out, err = _verify()
    assert _completed(code, out), err


def test_init_writes_config_for_detected_stack(in_repo: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stderr
    text = (in_repo / "counterproof.toml").read_text(encoding="utf-8")
    assert text.startswith("# Generated by `counterproof init`. Detected test runners: pytest")
    again = runner.invoke(app, ["init"])
    assert again.exit_code == 2
    assert "already exists" in again.stderr
    assert runner.invoke(app, ["init", "--force"]).exit_code == 0


def test_packaged_init_template_matches_example_config() -> None:
    root = Path(__file__).resolve().parents[2]
    packaged = root / "src" / "counterproof" / "data" / "counterproof.toml"
    example = root / "counterproof.example.toml"
    assert packaged.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
