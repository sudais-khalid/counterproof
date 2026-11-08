"""M7 integrations end to end: Claude Code Stop hook (coach mode), git pre-push hook,
`init --claude/--git-hook`, SARIF 2.1.0 and HTML reports, and the MCP server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
from mcp.client import Client
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.store import repo_hash, runs_root
from counterproof.integrations import claude_hook
from counterproof.integrations.mcp_server import build_server
from counterproof.llm.cache import CASSETTE_DIR_ENV, CASSETTE_MODE_ENV
from counterproof.models import RunManifest
from counterproof.report.bundle import load_bundle
from counterproof.report.coach import coach_hint
from tests.helpers import FIXTURE_REPOS, TESTS_DIR, build_repo, git, repo_state

pytestmark = pytest.mark.integration

SEED = "00" * 16
SCHEMA = json.loads((TESTS_DIR / "data" / "sarif-2.1.0.schema.json").read_text(encoding="utf-8"))
TRANSCRIPTS = TESTS_DIR / "fixtures" / "transcripts" / "c2"
runner = CliRunner()


def _hook(repo: Path, **fields: Any) -> dict[str, Any] | None:
    raw = json.dumps({"session_id": "s", "hook_event_name": "Stop", "cwd": str(repo), **fields})
    return claude_hook.run_hook(raw, default_cwd=repo)


def _no_mutant_text(text: str, run_dir: Path) -> None:
    bundle = load_bundle(run_dir)
    for m in bundle.mutants:
        for line in m.replacement.splitlines():
            if len(line.strip()) > 3:
                assert line.strip() not in text, m.id
        if m.hint:
            assert m.hint not in text


def _latest_run(cache: Path, repo: Path) -> Path:
    return sorted((runs_root(cache) / repo_hash(repo)).iterdir())[-1]


def test_m7_stop_hook_coaches_a_weak_change(tmp_path: Path, isolated_cache: Path) -> None:
    """PRD 13.1: WEAK -> Claude continues; the hint names the gap class and location only."""
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    before = repo_state(repo)
    out = _hook(repo, last_assistant_message="Capped the discount; tests pass.")
    assert out is not None and "decision" not in out  # guidance, not a hook error
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    reason = out["hookSpecificOutput"]["additionalContext"]
    assert reason.startswith("Counterproof: WEAK.")
    assert "`src/ledger/discounts.py:" in reason and "not detected by any test" in reason
    _no_mutant_text(reason, _latest_run(isolated_cache, repo))
    manifest = load_bundle(_latest_run(isolated_cache, repo)).manifest
    assert manifest.seed_commitment not in reason
    assert repo_state(repo) == before
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_m7_stop_hook_lets_a_proven_change_stop(tmp_path: Path) -> None:
    # T3 honest is PROVEN under the hook's fast profile (T1 honest is WEAK there: with only
    # 4 rule mutants and no LLM judge, its at-equality boundary survivor is not filtered).
    repo = build_repo(tmp_path / "repo", task="T3-allocate-remainder", variant="honest").path
    out = _hook(repo)
    assert out is not None and "decision" not in out
    assert out["systemMessage"].startswith("Counterproof: PROVEN (R-PROVEN): the tests detect")


def test_m7_stop_hook_refutes_a_false_claim(tmp_path: Path) -> None:
    """The final message comes from the hook input; the transcript is the session file."""
    overlay = FIXTURE_REPOS / "py_ledger" / "c2" / "broken-cap"
    repo = build_repo(
        tmp_path / "repo", task="T1-discount-cap", variant="honest", overlay=overlay
    ).path
    out = _hook(
        repo,
        transcript_path=str(TRANSCRIPTS / "masked-green.jsonl"),
        last_assistant_message="Done. The full test suite passes.",
    )
    assert out is not None and out["decision"] == "block"
    assert out["reason"].startswith("Counterproof: REFUTED.")
    assert "You said the tests pass, but selected tests fail on this change" in out["reason"]


def test_m7_stop_hook_is_silent_without_a_change(tmp_path: Path) -> None:
    repo = build_repo(tmp_path / "repo").path
    assert _hook(repo) is None
    assert _hook(repo, stop_hook_active=True) is None


@pytest.mark.parametrize(
    ("task", "variant"),
    [
        ("T1-discount-cap", "partial"),
        ("T2-round-half-up", "partial"),
        ("T3-allocate-remainder", "partial"),
        ("T5-cash-rounding", "honest"),
    ],
)
def test_m7_coach_hint_never_reveals_llm_mutants_hints_or_oracle(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch, task: str, variant: str
) -> None:
    """PRD 15.3 on recorded LLM runs: survivors with LLM code and judge hints, and (T3) a
    blind-oracle likely bug, never reach the coach hint."""
    monkeypatch.setenv(CASSETTE_DIR_ENV, str(TESTS_DIR / "cassettes" / "py_ledger"))
    monkeypatch.setenv(CASSETTE_MODE_ENV, "replay")
    repo = build_repo(tmp_path / "repo", task=task, variant=variant).path
    monkeypatch.chdir(repo)
    result = runner.invoke(
        app, ["verify", "--format", "json", "--seed", SEED, "--provider", "deepseek"]
    )
    manifest = RunManifest.model_validate_json(result.stdout)
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / manifest.run_id
    bundle = load_bundle(run_dir)
    hint = coach_hint(manifest, bundle.mutants)
    _no_mutant_text(hint, run_dir)
    assert SEED not in hint
    for probe in manifest.probes:
        for finding in probe.findings:
            if finding.rule_id == "BO-LIKELY-BUG":
                assert finding.message not in hint
                assert "independent check found a likely bug" in hint
    coach = runner.invoke(app, ["report", manifest.run_id, "--format", "md"])
    assert coach.exit_code == 0  # the audit view (for humans) still has everything


def test_m7_pre_push_hook_blocks_only_refuted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variant, code in (("hardcoded", 1), ("partial", 0)):
        built = build_repo(tmp_path / variant, task="T1-discount-cap", variant=variant)
        monkeypatch.chdir(built.path)
        line = f"refs/heads/agent {built.head_sha} refs/heads/agent {built.base_sha}\n"
        result = runner.invoke(app, ["hook", "pre-push", "origin", "url"], input=line)
        assert result.exit_code == code, result.stderr
        assert "counterproof: refs/heads/agent " in result.stderr
    stricter = runner.invoke(app, ["hook", "pre-push", "--fail-on", "weak"], input=line)
    assert stricter.exit_code == 1 and "push blocked (--fail-on weak)" in stricter.stderr


def test_m7_init_installs_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = build_repo(tmp_path / "repo").path
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["init", "--claude", "--git-hook"])
    assert result.exit_code == 0, result.stderr
    assert "added the Stop hook to" in result.stdout and "installed" in result.stdout
    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("hook claude-stop")
    assert (repo / ".git" / "hooks" / "pre-push").is_file()
    again = runner.invoke(app, ["init", "--claude"])
    assert "Stop hook already in" in again.stdout
    assert not (repo / "counterproof.toml").exists()  # integration flags only


def _sarif(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> dict[str, Any]:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "sarif", "--seed", SEED, *args])
    data: dict[str, Any] = json.loads(result.stdout)
    jsonschema.validate(data, SCHEMA)
    return data


def test_m7_sarif_validates_and_locates_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refuted = build_repo(tmp_path / "a", task="T1-discount-cap", variant="hardcoded").path
    run = _sarif(refuted, monkeypatch)["runs"][0]
    assert run["properties"]["verdict"] == "REFUTED"
    assert {r["ruleId"] for r in run["results"]} == {"FR-IN-07"}
    loc = run["results"][0]["locations"][0]["physicalLocation"]
    assert (
        loc["artifactLocation"]["uri"] == "src/ledger/discounts.py"
        and loc["region"]["startLine"] > 0
    )
    assert run["results"][0]["level"] == "error"
    assert [r["id"] for r in run["tool"]["driver"]["rules"]] == ["FR-IN-07"]

    weak = build_repo(tmp_path / "b", task="T1-discount-cap", variant="partial").path
    survivors = [
        r for r in _sarif(weak, monkeypatch)["runs"][0]["results"] if r["ruleId"] == "MU-SURVIVED"
    ]
    assert survivors and all(r["level"] == "warning" for r in survivors)
    assert "-> `" in survivors[0]["message"]["text"]  # audit view: code is shown to humans

    overlay = FIXTURE_REPOS / "py_ledger" / "c2" / "broken-cap"
    claimed = build_repo(
        tmp_path / "c", task="T1-discount-cap", variant="honest", overlay=overlay
    ).path
    run = _sarif(claimed, monkeypatch, "--transcript", str(TRANSCRIPTS / "false-green.jsonl"))[
        "runs"
    ][0]
    claim = next(r for r in run["results"] if r["ruleId"] == "CLAIM_FALSE_GREEN")
    assert claim["properties"]["approximateLocation"] is True and claim["level"] == "error"


def test_m7_html_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    monkeypatch.chdir(repo)
    first = RunManifest.model_validate_json(
        runner.invoke(app, ["verify", "--format", "json", "--seed", SEED]).stdout
    )
    out = tmp_path / "report.html"
    result = runner.invoke(app, ["verify", "--format", "html", "--seed", SEED, "--out", str(out)])
    assert result.exit_code == 10
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and '<span class="badge WEAK">WEAK</span>' in html
    assert html.count('<table class="diff">') >= 3  # one side-by-side diff per mutant
    assert "rate &gt;= 20" in html and "rate >= 20" not in html  # escaped code
    assert first.run_id in html and "(this run)" in html  # timeline of runs
    assert "<summary><code>run.log</code></summary>" in html  # expandable logs
    assert "<script" not in html and "http" not in html.split("<main>")[0].lower()


def _call(server: Any, tool: str, args: dict[str, Any]) -> str:
    async def go() -> str:
        async with Client(server) as client:
            result = await client.call_tool(tool, args)
            return "".join(c.text for c in result.content if hasattr(c, "text"))

    return anyio.run(go)


def test_m7_mcp_server_tools_and_redaction(tmp_path: Path, isolated_cache: Path) -> None:
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="partial").path
    server = build_server(repo)

    async def names() -> list[str]:
        async with Client(server) as client:
            return sorted(t.name for t in (await client.list_tools()).tools)

    assert anyio.run(names) == ["explain_mutant", "get_report", "verify_change"]
    summary = _call(server, "verify_change", {"profile": "fast"})
    assert summary.startswith("Counterproof: WEAK.") and "Run id: " in summary
    run_id = summary.split("Run id: ")[1].split()[0]
    run_dir = runs_root(isolated_cache) / repo_hash(repo) / run_id
    report = _call(server, "get_report", {"run_id": run_id})
    assert report.startswith("## Counterproof certificate: WEAK")
    assert "details withheld from agents" in report
    _no_mutant_text(report, run_dir)
    refused = _call(server, "get_report", {"run_id": run_id, "format": "json"})
    assert refused.startswith("error:") and "reveal_mutants" in refused
    mutant = load_bundle(run_dir).mutants[0]
    explained = _call(server, "explain_mutant", {"run_id": run_id, "mutant_id": mutant.id})
    assert "Code withheld" in explained and mutant.replacement.strip() not in explained
    assert _call(server, "verify_change", {"profile": "huge"}).startswith("error:")


def test_m7_mcp_reveal_needs_the_base_config(tmp_path: Path, isolated_cache: Path) -> None:
    """PRD 13.2/15.3: only a committed base config can reveal mutants to MCP clients."""
    repo = build_repo(
        tmp_path / "repo", task="T1-discount-cap", variant="partial", commit_head=False
    ).path
    (repo / "counterproof.toml").write_text("[mcp]\nreveal_mutants = true\n", encoding="utf-8")
    server = build_server(repo)
    run_id = _call(server, "verify_change", {"profile": "fast"}).split("Run id: ")[1].split()[0]
    assert _call(server, "get_report", {"run_id": run_id, "format": "json"}).startswith("error:")

    # A human commits the setting on the default branch; the agent's work stays uncommitted.
    (repo / "counterproof.toml").unlink()
    git(repo, "checkout", "-q", "main")
    (repo / "counterproof.toml").write_text("[mcp]\nreveal_mutants = true\n", encoding="utf-8")
    git(repo, "add", "counterproof.toml")
    git(repo, "commit", "-q", "-m", "allow reveal")
    git(repo, "checkout", "-q", "agent/T1-discount-cap")
    git(repo, "merge", "-q", "--ff-only", "main")
    revealed = _call(build_server(repo), "get_report", {"run_id": run_id, "format": "json"})
    assert RunManifest.model_validate_json(revealed).run_id == run_id
    mutant = load_bundle(runs_root(isolated_cache) / repo_hash(repo) / run_id).mutants[0]
    explained = _call(
        build_server(repo), "explain_mutant", {"run_id": run_id, "mutant_id": mutant.id}
    )
    assert f"({mutant.id})" in explained and "Code withheld" not in explained
