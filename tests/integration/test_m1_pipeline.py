"""M1 acceptance on the py_ledger fixture: hunks, selected tests, 3 baseline runs, flaky
detection, HEAD_RED, secret scrubbing, and an untouched user working tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.store import EvidenceStore, repo_hash, runs_root
from counterproof.models import BaselineReport, RunManifest, Selection
from tests.helpers import build_repo, git, repo_state

pytestmark = pytest.mark.integration

runner = CliRunner()
DISCOUNT_TESTS = {
    "tests/test_discounts.py::test_percent_discount_without_cap",
    "tests/test_discounts.py::test_discount_below_cap_is_unchanged",
    "tests/test_discounts.py::test_percent_must_be_a_percentage",
    "tests/test_discounts.py::test_cap_currency_must_match",
    "tests/test_discounts.py::test_discount_is_capped",
    "tests/test_discounts.py::test_discount_exactly_at_cap",
    "tests/test_discounts.py::test_large_discount_on_small_order_is_capped",
}
INVOICE_TOTAL_TESTS = {
    "tests/test_invoice.py::test_total_without_discount_or_tax",
    "tests/test_invoice.py::test_total_with_uncapped_discount_and_tax",
}


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> RunManifest:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", *args])
    assert result.exit_code in {40, 0, 10, 20, 30}, result.stderr
    return RunManifest.model_validate_json(result.stdout)


def _run_dir(cache: Path, repo: Path, manifest: RunManifest) -> Path:
    return runs_root(cache) / repo_hash(repo) / manifest.run_id


def _artefact(cache: Path, repo: Path, manifest: RunManifest, name: str) -> Any:
    return json.loads((_run_dir(cache, repo, manifest) / name).read_text(encoding="utf-8"))


@pytest.fixture
def t1_honest(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "repo", task="T1-discount-cap", variant="honest").path


def test_m1_change_selection_and_three_baseline_runs(
    t1_honest: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = repo_state(t1_honest)
    manifest = _verify(t1_honest, monkeypatch)

    # FR-CR-01..05: default base is the merge-base; head is the (clean) working tree.
    change = manifest.change
    assert change.base_sha == git(t1_honest, "rev-parse", "main").strip()
    assert change.head_ref == "WORKTREE"
    assert {(f.path, f.role, f.status) for f in change.files} == {
        ("src/ledger/discounts.py", "source", "modified"),
        ("tests/test_discounts.py", "test", "modified"),
    }
    assert (change.intent, change.intent_source) == ("bugfix", "rule")
    assert change.task_text and change.task_text.startswith("Bug: percent_discount ignores the cap")
    source_hunks = [h for h in change.hunks if h.path == "src/ledger/discounts.py"]
    assert [(h.new_start, h.new_len) for h in source_hunks] == [(18, 2)]

    # FR-IS-01: coverage-selected tests plus the whole changed test file, nothing unrelated.
    selection = Selection.model_validate(
        _artefact(isolated_cache, t1_honest, manifest, "selection.json")
    )
    assert selection.coverage_available
    assert set(selection.tests) == DISCOUNT_TESTS | INVOICE_TOTAL_TESTS
    assert set(selection.covering) >= INVOICE_TOTAL_TESTS
    assert "tests/test_invoice.py::test_subtotal_sums_line_items" not in selection.tests
    # Raised before reaching the changed lines, so it does not cover them.
    assert "tests/test_discounts.py::test_cap_currency_must_match" not in selection.covering

    # FR-IS-03: three baseline runs on head, all green, no flakiness.
    report = BaselineReport.model_validate(
        _artefact(isolated_cache, t1_honest, manifest, "baseline.json")
    )
    assert report.runs == 3
    assert report.status == "ok"
    assert report.flaky == [] and report.failing == []
    assert set(report.durations) == set(selection.tests)
    assert all(outcomes == ["pass"] * 3 for outcomes in report.outcomes.values())
    for i in (1, 2, 3):
        assert f"junit/baseline-{i}.xml" in manifest.artefacts
    assert EvidenceStore.open(_run_dir(isolated_cache, t1_honest, manifest)).verify() == []

    assert manifest.verdict.verdict == "INCONCLUSIVE"
    assert manifest.verdict.rule_id == "NOT_IMPLEMENTED"
    assert "x 3 runs" in manifest.verdict.reasons[0]

    # FR-WS-01 / engineering rule 1: the user's tree, index, refs and stash are untouched,
    # and every worktree was removed.
    assert repo_state(t1_honest) == before
    assert git(t1_honest, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not (_run_dir(isolated_cache, t1_honest, manifest) / "ws" / "head").exists()


def test_m1_uncommitted_worktree_with_untracked_test_file(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(
        tmp_path / "repo", task="T1-discount-cap", variant="honest", commit_head=False
    ).path
    (repo / "tests" / "test_cap_extra.py").write_text(
        "from ledger.discounts import percent_discount\nfrom ledger.money import Money\n\n\n"
        "def test_tiny_cap():\n"
        '    cap = Money.of("1.00")\n'
        '    assert percent_discount(Money.of("10.00"), 50, cap=cap) == cap\n',
        encoding="utf-8",
    )
    before = repo_state(repo)
    manifest = _verify(repo, monkeypatch, "--task", "Cap the discount")

    assert manifest.change.base_sha == git(repo, "rev-parse", "HEAD").strip()
    assert manifest.change.head_snapshot_sha != manifest.change.base_sha
    added = {f.path: f.status for f in manifest.change.files}
    assert added["tests/test_cap_extra.py"] == "added"
    selection = Selection.model_validate(
        _artefact(isolated_cache, repo, manifest, "selection.json")
    )
    assert "tests/test_cap_extra.py::test_tiny_cap" in selection.tests
    assert manifest.change.task_text == "Cap the discount"
    assert repo_state(repo) == before


def test_fr_is_05_head_red(
    t1_honest: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = t1_honest / "tests" / "test_discounts.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'Money.of("40.00")) == Money.of("40.00")', 'Money.of("40.00")) == Money.of("45.00")'
        ),
        encoding="utf-8",
    )
    manifest = _verify(t1_honest, monkeypatch)
    assert manifest.verdict.rule_id == "R-INC-HEADRED"
    assert manifest.verdict.exit_code == 40
    report = BaselineReport.model_validate(
        _artefact(isolated_cache, t1_honest, manifest, "baseline.json")
    )
    assert report.failing == [
        "tests/test_discounts.py::test_large_discount_on_small_order_is_capped"
    ]


FLIP_TEST = """\
from pathlib import Path

COUNTER = Path({counter!r})


def test_flips_every_run():
    n = int(COUNTER.read_text()) if COUNTER.exists() else 0
    COUNTER.write_text(str(n + 1))
    assert n % 2 == 0
"""


def test_fr_is_03_real_flaky_test_is_detected_and_excluded(
    t1_honest: Path, tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = (tmp_path / "counter").as_posix()
    (t1_honest / "tests" / "test_flip.py").write_text(
        FLIP_TEST.format(counter=counter), encoding="utf-8"
    )
    manifest = _verify(t1_honest, monkeypatch)
    report = BaselineReport.model_validate(
        _artefact(isolated_cache, t1_honest, manifest, "baseline.json")
    )
    assert report.flaky == ["tests/test_flip.py::test_flips_every_run"]
    assert report.status == "ok"  # 1 of 10 selected tests is below the 0.2 threshold
    assert "tests/test_flip.py::test_flips_every_run" not in report.stable
    assert manifest.verdict.rule_id == "NOT_IMPLEMENTED"
    assert "1 flaky excluded" in manifest.verdict.reasons[0]


def test_fr_is_04_flaky_suite_is_inconclusive(
    t1_honest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = (tmp_path / "counter").as_posix()
    (t1_honest / "tests" / "test_flip.py").write_text(
        FLIP_TEST.format(counter=counter), encoding="utf-8"
    )
    (t1_honest / "counterproof.toml").write_text("[flaky]\nmax_ratio = 0.05\n", encoding="utf-8")
    manifest = _verify(t1_honest, monkeypatch, "--config-from", "worktree")
    assert manifest.verdict.rule_id == "R-INC-FLAKY"
    assert "FLAKY_SUITE" in manifest.verdict.reasons[0]


ENV_PROBE = """\
import os


def test_child_environment_is_scrubbed():
    assert "CP_FAKE_API_KEY" not in os.environ
    assert "GITHUB_TOKEN" not in os.environ
    assert not [k for k in os.environ if k.upper().startswith("COUNTERPROOF_")]
    assert os.environ.get("LEDGER_DB_URL") == "sqlite://test"
"""


def test_fr_ws_04_secrets_never_reach_the_test_process(
    t1_honest: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CP_FAKE_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_leak")
    monkeypatch.setenv("LEDGER_DB_URL", "sqlite://test")
    (t1_honest / "tests" / "test_env_probe.py").write_text(ENV_PROBE, encoding="utf-8")
    (t1_honest / "counterproof.toml").write_text(
        '[env]\npass = ["LEDGER_DB_URL"]\n', encoding="utf-8"
    )
    manifest = _verify(t1_honest, monkeypatch, "--config-from", "worktree")
    report = BaselineReport.model_validate(
        _artefact(isolated_cache, t1_honest, manifest, "baseline.json")
    )
    assert (
        report.outcomes["tests/test_env_probe.py::test_child_environment_is_scrubbed"]
        == ["pass"] * 3
    )
    assert report.status == "ok"


def test_fr_cr_07_docs_only_change_is_no_code_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo").path
    git(repo, "checkout", "-q", "-b", "agent/docs")
    (repo / "README.md").write_text("# py-ledger\n\nBetter docs.\n", encoding="utf-8")
    git(repo, "commit", "-qam", "Improve the README")
    manifest = _verify(repo, monkeypatch, "--head", "HEAD")
    assert manifest.verdict.rule_id == "R-INC-BUILD"
    assert "NO_CODE_CHANGE" not in manifest.change.flags  # docs are text, not binary
    assert manifest.change.intent == "non_code"


def test_fr_cr_07_empty_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = build_repo(tmp_path / "repo").path
    manifest = _verify(repo, monkeypatch)
    assert "NO_CODE_CHANGE" in manifest.change.flags
    assert manifest.verdict.rule_id == "R-INC-BUILD"
    assert "NO_CODE_CHANGE" in manifest.verdict.reasons[0]


def test_fr_ws_07_keep_then_gc_prunes_worktrees(
    t1_honest: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _verify(t1_honest, monkeypatch, "--keep")
    run_dir = _run_dir(isolated_cache, t1_honest, manifest)
    assert (run_dir / "ws" / "head" / "src" / "ledger" / "discounts.py").is_file()
    assert git(t1_honest, "worktree", "list", "--porcelain").count("worktree ") == 3
    result = runner.invoke(app, ["gc", "--older-than", "0s"])
    assert result.exit_code == 0, result.stderr
    assert not run_dir.exists()
    assert git(t1_honest, "worktree", "list", "--porcelain").count("worktree ") == 1
