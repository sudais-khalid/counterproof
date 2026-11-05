"""Run orchestration (PRD section 6.3).

M1 scope: resolve refs and the change, snapshot the working tree, build base and head
worktrees, detect the stack, select impacted tests and run the head baseline. Probes arrive
from M2; until the verdict engine lands in M3, the early INCONCLUSIVE outcomes here use the
section 8 rule ids (R-INC-BUILD, R-INC-FLAKY, R-INC-HEADRED) and everything else is reported
as ``INCONCLUSIVE / NOT_IMPLEMENTED``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from counterproof import __version__, git
from counterproof.adapters.base import CollectionError
from counterproof.adapters.detect import detect_adapters
from counterproof.change.resolver import NO_CODE_CHANGE, commit_message_for, resolve_change
from counterproof.change.task import resolve_task_text
from counterproof.config import CONFIG_FILENAME, Config, build_config, load_config, parse_toml
from counterproof.errors import UsageError
from counterproof.evidence.seed import (
    SECRET_FILENAME,
    derive_seed,
    load_or_create_secret,
    parse_seed_hex,
    seed_commitment,
)
from counterproof.evidence.store import EvidenceStore, cache_root, runs_root
from counterproof.impact.baseline import analyse_baseline
from counterproof.impact.selector import select_tests
from counterproof.log import attach_run_log, detach_run_log, get_logger
from counterproof.models import (
    WORKTREE,
    BaselineReport,
    ChangeSet,
    Intent,
    RunManifest,
    RunResult,
    Verdict,
)
from counterproof.workspace.env import dropped_names, scrub_env
from counterproof.workspace.manager import WorkspaceManager

log = get_logger("orchestrator")

RUN_LOG: Final = "run.log"
SEED_FILE: Final = "seed"
ConfigFrom = Literal["base", "worktree"]


@dataclass(frozen=True)
class VerifyRequest:
    cwd: Path
    base: str | None = None
    head: str | None = None
    task_text: str | None = None
    task_file_text: str | None = None
    intent: Intent | None = None
    transcript: Path | None = None
    seed_hex: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    keep: bool = False
    config_from: ConfigFrom = "base"


@dataclass(frozen=True)
class VerifyOutcome:
    manifest: RunManifest
    manifest_sha256: str
    run_dir: Path
    config: Config


def load_run_config(
    repo: Path, base_sha: str, config_from: ConfigFrom, overrides: Mapping[str, Any]
) -> Config:
    """PRD 15.3: by default the config comes from the base commit, so the change under test
    cannot loosen its own thresholds. ``worktree`` reads the file on disk instead."""
    if config_from == "worktree":
        return load_config(repo / CONFIG_FILENAME, overrides)
    source = f"{base_sha[:12]}:{CONFIG_FILENAME}"
    text = git.show_file(repo, base_sha, CONFIG_FILENAME)
    data = parse_toml(text, source=source) if text is not None else {}
    return build_config(data, overrides, source=source if text is not None else "<defaults>")


def run_verify(req: VerifyRequest, *, cache: Path | None = None) -> VerifyOutcome:
    cache = cache if cache is not None else cache_root()
    repo = git.repo_root(req.cwd)
    # FR-CR-01: explicit refs win; otherwise merge-base with the default branch or HEAD.
    base_sha = git.resolve_commit(repo, req.base) if req.base else git.default_base(repo)
    head_is_worktree = req.head is None or req.head == WORKTREE
    head_commit = None if head_is_worktree else git.resolve_commit(repo, req.head or "HEAD")
    if req.transcript is not None and not req.transcript.is_file():
        raise UsageError(f"transcript not found: {req.transcript}")
    config = load_run_config(repo, base_sha, req.config_from, req.overrides)

    store = EvidenceStore.create(repo, runs_dir=runs_root(cache))
    handler = attach_run_log(store.path(RUN_LOG))
    try:
        log.info("run %s for %s (base %s, head %s)", store.run_id, repo, base_sha, req.head)
        seed = (
            parse_seed_hex(req.seed_hex)
            if req.seed_hex
            else derive_seed(load_or_create_secret(cache / SECRET_FILENAME), store.run_id)
        )
        # The seed stays in the user cache and is never listed as an artefact.
        store.write_text(SEED_FILE, seed.hex(), artefact=False)
        store.write_json("config.json", config.to_toml_dict())

        with WorkspaceManager(repo, store.run_dir / "ws", keep=req.keep) as workspaces:
            head_sha = head_commit or workspaces.snapshot_worktree()
            head_ref = WORKTREE if head_is_worktree else head_sha
            task = resolve_task_text(
                cli_text=req.task_text,
                file_text=req.task_file_text,
                transcript=req.transcript,
                commit_message=commit_message_for(repo, base_sha, head_ref),
            )
            log.info("task text from %s", task.source)
            change = resolve_change(
                repo,
                base_sha=base_sha,
                head_ref=head_ref,
                head_sha=head_sha,
                task=task,
                transcript=req.transcript,
                user_intent=req.intent,
            )
            store.write_json("change.json", change)
            verdict = _preflight(store, workspaces, change, config)
            if req.keep:
                for ws in workspaces.workspaces.values():
                    log.warning("kept worktree %s: %s", ws.name, ws.root)

        manifest = RunManifest(
            run_id=store.run_id,
            tool_version=__version__,
            created_at=datetime.now(UTC),
            seed_commitment=seed_commitment(seed),
            config_sha256=config.sha256(),
            change=change,
            probes=[],
            verdict=verdict,
        )
        log.info("verdict %s (%s)", verdict.verdict, verdict.rule_id)
    finally:
        detach_run_log(handler)
    store.track(RUN_LOG)
    sealed, digest = store.seal(manifest)
    return VerifyOutcome(sealed, digest, store.run_dir, config)


def _inconclusive(rule_id: str, *reasons: str) -> Verdict:
    return Verdict.make("INCONCLUSIVE", rule_id, list(reasons))


def _preflight(
    store: EvidenceStore, workspaces: WorkspaceManager, change: ChangeSet, config: Config
) -> Verdict:
    """Workspaces, stack detection, impact selection and baseline (sections 6.3 steps 2-5)."""
    if NO_CODE_CHANGE in change.flags:
        # FR-CR-07
        return _inconclusive("R-INC-BUILD", "NO_CODE_CHANGE: the change has no code to verify")

    head = workspaces.add("head", change.head_snapshot_sha)
    workspaces.add("base", change.base_sha)
    adapters = detect_adapters(head.root, env_pass=config.env.pass_)
    if not adapters:
        return _inconclusive("R-INC-BUILD", "no supported test runner detected (pytest in M1)")
    adapter = adapters[0]
    dropped = dropped_names(os.environ, scrub_env(os.environ, config.env.pass_))
    log.info("child env: dropped %d variables (names in run log)", len(dropped))
    log.debug("dropped env names: %s", ", ".join(dropped))

    prep = adapter.prepare(head)
    if not prep.ok:
        return _inconclusive("R-INC-BUILD", f"head cannot prepare: {prep.message}")
    try:
        all_tests = adapter.list_tests(head)
    except CollectionError as exc:
        store.write_text("logs/collect-head.log", exc.output)
        return _inconclusive("R-INC-BUILD", f"head cannot collect tests: {exc}")

    timeout = config.impact.run_timeout_s
    coverage = adapter.coverage_map(
        head,
        [t.nodeid for t in all_tests],
        timeout_s=timeout,
        junit_path=store.path("junit/coverage-head.xml"),
        coverage_file=store.path("coverage/head.coverage"),
    )
    _track_existing(
        store, "junit/coverage-head.xml", "junit/coverage-head.log", "coverage/head.coverage"
    )
    sources = [f.path for f in change.files if f.role == "source" and f.status != "deleted"]
    selection = select_tests(
        change,
        all_tests,
        coverage,
        adapter.related_tests(sources, all_tests),
        max_tests=config.impact.max_tests,
    )
    store.write_json("selection.json", selection)
    if not selection.tests:
        return _inconclusive("R-INC-BUILD", "no selected tests for the changed lines")

    runs: list[RunResult] = []
    for i in range(1, config.impact.baseline_runs + 1):
        junit = f"junit/baseline-{i}.xml"
        runs.append(
            adapter.run(
                head,
                selection.tests,
                timeout_s=timeout,
                fail_fast=False,
                junit_path=store.path(junit),
            )
        )
        _track_existing(store, junit, f"junit/baseline-{i}.log")
    store.write_json("baseline-runs.json", [r.model_dump(mode="json") for r in runs])
    report = analyse_baseline(
        runs,
        selection.tests,
        known_flaky=config.flaky.known,
        max_ratio=config.flaky.max_ratio,
    )
    store.write_json("baseline.json", report)
    return _baseline_verdict(report, config)


def _baseline_verdict(report: BaselineReport, config: Config) -> Verdict:
    if report.status == "flaky_suite":
        return _inconclusive(
            "R-INC-FLAKY",
            f"FLAKY_SUITE: {len(report.flaky)} of {len(report.selected)} selected tests are "
            f"flaky (ratio {report.flaky_ratio:.2f} > {config.flaky.max_ratio})",
        )
    if report.status == "head_red":
        shown = ", ".join(report.failing[:5]) + (" ..." if len(report.failing) > 5 else "")
        return _inconclusive("R-INC-HEADRED", f"HEAD_RED: {len(report.failing)} failing: {shown}")
    if report.status == "no_tests":
        return _inconclusive("R-INC-BUILD", "no selected tests for the changed lines")
    flaky_note = f"; {len(report.flaky)} flaky excluded" if report.flaky else ""
    return Verdict.make(
        "INCONCLUSIVE",
        "NOT_IMPLEMENTED",
        [
            f"baseline green: {len(report.stable)} stable selected tests x {report.runs} runs"
            f"{flaky_note}",
            "probes are not implemented yet (P1 and P2 arrive in M2)",
        ],
        confidence_note="Change resolved and baseline run; no probe has judged the change.",
    )


def _track_existing(store: EvidenceStore, *paths: str) -> None:
    for rel in paths:
        if store.path(rel).is_file():
            store.track(rel)
