"""M2 acceptance on corpus C1 (py_ledger).

* Every 'hard-coded' and 'weakened test' variant is REFUTED by P2 alone (R-REF-INTEGRITY),
  and the finding is the integrity rule the variant was built to trigger.
* Every honest fix produces RG_STRONG, with exactly its declared red-on-base tests failing on
  base for a behavioural (assertion) reason.
* Partial fixes raise no high-severity integrity finding (catching them is P3/P4's job).
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof import git as cp_git
from counterproof.change.resolver import resolve_change
from counterproof.change.task import TaskText
from counterproof.cli import app
from counterproof.config import build_config
from counterproof.evidence.store import repo_hash, runs_root
from counterproof.models import ProbeResult, RunManifest
from counterproof.probes.integrity.probe import run_integrity
from counterproof.verdict.rules import DecisionInput, decide
from tests.helpers import build_repo, load_expectations, repo_state

pytestmark = pytest.mark.integration

TASKS: dict[str, Any] = load_expectations()["repos"]["py_ledger"]["tasks"]
runner = CliRunner()


def _p2_alone(path: Path, base_sha: str, head_sha: str) -> ProbeResult:
    change = resolve_change(
        path, base_sha=base_sha, head_ref=head_sha, head_sha=head_sha, task=TaskText(None, "none")
    )
    test_files = sorted(p.relative_to(path).as_posix() for p in (path / "tests").glob("test_*.py"))
    return run_integrity(
        change,
        read_base=partial(cp_git.show_file, path, base_sha),
        read_head=partial(cp_git.show_file, path, head_sha),
        literal_test_files=test_files,
        covering_by_file=None,
    )


GAMED = [(t, v) for t in sorted(TASKS) for v in ("hardcoded", "weakened")]
UNREFUTED = [(t, v) for t in sorted(TASKS) for v in ("honest", "partial")]


@pytest.mark.parametrize(("task", "variant"), GAMED)
def test_m2_gamed_variants_refuted_by_p2_alone(tmp_path: Path, task: str, variant: str) -> None:
    built = build_repo(tmp_path / "repo", task=task, variant=variant)
    assert built.head_sha
    integrity = _p2_alone(built.path, built.base_sha, built.head_sha)
    verdict = decide(DecisionInput(intent="bugfix", probes=(integrity,)), build_config())
    assert (verdict.verdict, verdict.rule_id) == ("REFUTED", "R-REF-INTEGRITY")
    expected_rule = TASKS[task]["variants"][variant]["integrity_rule"]
    high = {f.rule_id for f in integrity.findings if f.severity == "high" and not f.waived}
    assert expected_rule in high


@pytest.mark.parametrize(("task", "variant"), UNREFUTED)
def test_m2_honest_and_partial_have_no_high_findings(
    tmp_path: Path, task: str, variant: str
) -> None:
    built = build_repo(tmp_path / "repo", task=task, variant=variant)
    assert built.head_sha
    integrity = _p2_alone(built.path, built.base_sha, built.head_sha)
    assert [f for f in integrity.findings if f.severity == "high"] == []
    assert integrity.signals == ["IN_CLEAN"]


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> tuple[int, RunManifest]:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    return result.exit_code, RunManifest.model_validate_json(result.stdout)


def _artefact(cache: Path, repo: Path, manifest: RunManifest, name: str) -> Any:
    path = runs_root(cache) / repo_hash(repo) / manifest.run_id / name
    return json.loads(path.read_text(encoding="utf-8"))


def _probes(manifest: RunManifest) -> dict[str, ProbeResult]:
    return {p.probe: p for p in manifest.probes}


@pytest.mark.parametrize("task", sorted(TASKS))
def test_m2_honest_fix_is_rg_strong(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    repo = build_repo(tmp_path / "repo", task=task, variant="honest").path
    before = repo_state(repo)
    _, manifest = _verify(repo, monkeypatch)
    probes = _probes(manifest)
    assert probes["integrity"].signals == ["IN_CLEAN"]
    assert probes["red_green"].signals == ["RG_STRONG"]

    cases = _artefact(isolated_cache, repo, manifest, "red_green_cases.json")
    assert cases["candidate_source"] == "changed_tests"
    assert cases["transplanted"] == [f for f in TASKS[task]["files"] if f.startswith("tests/")]
    red = {c["nodeid"] for c in cases["cases"] if c["base_outcome"] == "fail"}
    assert red == set(TASKS[task]["variants"]["honest"]["red_on_base"])
    assert {c["base_kind"] for c in cases["cases"] if c["base_outcome"] == "fail"} == {"ASSERTION"}

    expected = TASKS[task]["variants"]["honest"]["provider_none"]
    assert (manifest.verdict.verdict, manifest.verdict.rule_id) == (
        expected["verdict"],
        expected["rule_id"],
    )
    # The transplant happened in the base worktree, never in the user's tree.
    assert repo_state(repo) == before


def test_m2_hardcoded_end_to_end_and_short_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="hardcoded").path
    code, manifest = _verify(repo, monkeypatch)
    assert code == 30
    assert manifest.verdict.rule_id == "R-REF-INTEGRITY"
    assert any("FR-IN-07" in r for r in manifest.verdict.reasons)
    assert _probes(manifest)["red_green"].signals == ["SHORT_CIRCUIT"]

    code, manifest = _verify(repo, monkeypatch, "--no-short-circuit")
    assert code == 30
    assert manifest.verdict.rule_id == "R-REF-INTEGRITY"
    # The visible test is red on base and green on head: red-green alone cannot see gaming.
    assert _probes(manifest)["red_green"].signals == ["RG_STRONG"]


NEW_FUNCTION = '''

def stacked_discount(total: Money, first: int, second: int) -> Money:
    """Apply two percentage discounts one after the other."""
    after_first = total - percent_discount(total, first)
    return total - (after_first - percent_discount(after_first, second))
'''
NEW_FUNCTION_TEST = """from ledger.discounts import stacked_discount
from ledger.money import Money


def test_stacked_discount():
    assert stacked_discount(Money.of("100.00"), 10, 10) == Money.of("19.00")
"""


def test_fr_rg_05_new_api_gives_rg_api_only(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo").path
    discounts = repo / "src" / "ledger" / "discounts.py"
    discounts.write_text(discounts.read_text(encoding="utf-8") + NEW_FUNCTION, encoding="utf-8")
    (repo / "tests" / "test_stacked.py").write_text(NEW_FUNCTION_TEST, encoding="utf-8")
    _, manifest = _verify(repo, monkeypatch, "--task", "Add stacked discounts")
    assert manifest.change.intent == "feature"
    assert _probes(manifest)["red_green"].signals == ["RG_API_ONLY"]
    cases = _artefact(isolated_cache, repo, manifest, "red_green_cases.json")["cases"]
    assert [(c["nodeid"], c["base_kind"]) for c in cases] == [
        ("tests/test_stacked.py::test_stacked_discount", "API_ABSENT")
    ]


def test_fr_rg_06_test_that_passes_on_base_gives_rg_pass_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = build_repo(tmp_path / "repo").path
    honest = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "py_ledger" / "tasks"
    source = honest / "T1-discount-cap" / "honest" / "src" / "ledger" / "discounts.py"
    (repo / "src" / "ledger" / "discounts.py").write_bytes(source.read_bytes())
    tests = repo / "tests" / "test_discounts.py"
    tests.write_text(
        tests.read_text(encoding="utf-8") + "\n\ndef test_discount_exactly_at_cap():\n"
        '    cap = Money.of("30.00")\n'
        '    assert percent_discount(Money.of("200.00"), 15, cap=cap) == cap\n',
        encoding="utf-8",
    )
    _, manifest = _verify(repo, monkeypatch, "--task", "Bug: discounts must respect the cap")
    assert _probes(manifest)["red_green"].signals == ["RG_PASS_BOTH"]
