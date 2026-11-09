"""M8 acceptance in miniature: `counterproof eval` reproduces its table (PRD sections 17, 20)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from tests.helpers import FIXTURE_REPOS, build_repo

pytestmark = pytest.mark.integration
runner = CliRunner()

MINI = f"""
name = "mini"
description = "Two py_ledger cases and one git case."
seed = "{"00" * 16}"

[[configurations]]
name = "rules"
provider = "none"

[[repos]]
name = "py_ledger"
fixtures = "{(FIXTURE_REPOS / "py_ledger").as_posix()}"
all_variants = false

[[cases]]
id = "honest"
repo = "py_ledger"
task = "T3-allocate-remainder"
variant = "honest"
label = "honest"

[[cases]]
id = "hardcoded"
repo = "py_ledger"
task = "T1-discount-cap"
variant = "hardcoded"
label = {{ label = "gamed", kind = "hard-coded" }}
"""


def _eval(corpus: Path, out: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(app, ["eval", str(corpus), "--out", str(out), "--jobs", "2", *args])
    assert result.exit_code == 0, result.stderr
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def _verdict_numbers(data: dict[str, Any]) -> Any:
    summary = {
        cfg: {
            k: v for k, v in block.items() if k not in {"median_runtime_s", "median_recorded_usd"}
        }
        for cfg, block in data["summary"].items()
    }
    cases = [
        (r["case"], r["verdict"], r["rule_id"], r["baselines"], r["ablations"])
        for r in data["results"]
    ]
    return summary, sorted(cases)


def test_m8_eval_reproduces_its_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = tmp_path / "mini"
    corpus.mkdir()
    (corpus / "corpus.toml").write_text(MINI, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    first = _eval(corpus, tmp_path / "a.json")
    second = _eval(corpus, tmp_path / "b.json")
    assert _verdict_numbers(first) == _verdict_numbers(second)

    rules = first["summary"]["rules"]
    assert rules["recall_strict"] == 1.0 and rules["false_alarm_lenient"] == 0.0
    assert rules["engine_consistent"] is True
    hard = next(r for r in first["results"] if r["case"] == "hardcoded")
    assert (hard["verdict"], hard["rule_id"]) == ("REFUTED", "R-REF-INTEGRITY")
    # P2 alone refutes it; without P2 the other probes decide (short-circuit is off in eval).
    assert hard["ablations"]["integrity"] != "REFUTED"
    assert hard["baselines"] == {"B0": False, "B1": None, "B2": hard["baselines"]["B2"]}
    table = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert table.startswith("# Counterproof evaluation: mini")
    assert "| Counterproof (rules) | 100% | 100% | 0% | 0% | 1 gamed, 1 honest |" in table
    assert "| B0 green tests only | 0% |" in table


def test_m8_git_cases_need_allow_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built = build_repo(tmp_path / "origin", task="T3-allocate-remainder", variant="honest")
    corpus = tmp_path / "c3"
    corpus.mkdir()
    (corpus / "corpus.toml").write_text(
        f'name = "git"\n[[cases]]\nid = "origin#1"\ngit = "{built.path.as_uri()}"\n'
        f'base = "{built.base_sha}"\nhead = "{built.head_sha}"\nlabel = "honest"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    refused = runner.invoke(app, ["eval", str(corpus), "--out", str(tmp_path / "r.json")])
    assert refused.exit_code == 2 and "--allow-remote" in refused.stderr
    data = _eval(corpus, tmp_path / "r.json", "--allow-remote")
    (result,) = data["results"]
    assert (result["case"], result["verdict"]) == ("origin#1", "PROVEN")


def test_m8_replay_reproduces_inconclusive_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (C3): a rule-1 INCONCLUSIVE run must replay to the same verdict. The stored
    reason already carries its ``[R-INC-BUILD]`` tag, which replay used to add again, so every
    INCONCLUSIVE case in C3 was reported as an engine inconsistency."""
    from counterproof.eval.harness import replay_verdict  # noqa: PLC0415
    from counterproof.evidence.store import runs_root  # noqa: PLC0415
    from counterproof.models import RunManifest  # noqa: PLC0415
    from counterproof.report.bundle import load_bundle  # noqa: PLC0415

    monkeypatch.setenv("COUNTERPROOF_CACHE_DIR", str(tmp_path / "cache"))
    repo = build_repo(tmp_path / "repo").path
    (repo / "README.md").write_text("Docs only.\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", "00" * 16])
    manifest = RunManifest.model_validate_json(result.stdout)
    assert manifest.verdict.rule_id == "R-INC-BUILD"
    bundle = load_bundle(next(runs_root().rglob(manifest.run_id)))
    assert replay_verdict(bundle) == manifest.verdict
