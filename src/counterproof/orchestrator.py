"""Run orchestration (PRD section 6.3).

Resolve refs and the change, snapshot the working tree, build base and head worktrees,
detect the stack, select impacted tests, run the head baseline, then the probes cheapest
first: P5 claim audit (before the early verdict, so rule 3 sees it, and even on a red
head), P2 integrity, P1 red-green, P3 mutants, P4 blind oracle. Every decision goes through
the pure :func:`counterproof.verdict.rules.decide`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Final, Literal

from counterproof import __version__, git
from counterproof.adapters.base import Adapter, CollectionError, CoverageMap
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
from counterproof.impact.baseline import analyse_baseline, mutant_timeout_s
from counterproof.impact.selector import select_tests
from counterproof.llm.factory import build_session
from counterproof.llm.provider import ModelNotConfiguredError, choose_oracle_models
from counterproof.llm.redact import may_send
from counterproof.llm.roles import LLMSession
from counterproof.log import attach_run_log, detach_run_log, get_logger
from counterproof.models import (
    WORKTREE,
    BaselineReport,
    ChangeSet,
    Intent,
    LLMUsage,
    ProbeResult,
    RunManifest,
    RunResult,
    Selection,
    Verdict,
)
from counterproof.probes.blind_oracle.interface import (
    changed_function_sources,
    extract_interface,
)
from counterproof.probes.blind_oracle.probe import (
    BO_NO_INTERFACE,
    BO_NO_LLM,
    run_blind_oracle,
    skipped,
)
from counterproof.probes.claim_audit.parsers import Transcript, load_transcript
from counterproof.probes.claim_audit.probe import run_claim_audit
from counterproof.probes.integrity.probe import changed_test_file_pairs, run_integrity
from counterproof.probes.integrity.python_rules import TestFilePair, changed_test_ids
from counterproof.probes.mutants.fuzz import make_fuzzer
from counterproof.probes.mutants.probe import LLMContext, run_mutation_probe
from counterproof.probes.pyast import Literal as CodeLiteral
from counterproof.probes.pyast import added_symbols, analyse_test_file, module_name
from counterproof.probes.red_green import run_red_green
from counterproof.verdict.rules import DecisionInput, decide, early_verdict
from counterproof.workspace.env import dropped_names, scrub_env
from counterproof.workspace.manager import Workspace, WorkspaceManager

log = get_logger("orchestrator")

RUN_LOG: Final = "run.log"
ORACLE_RUN_TIMEOUT_S: Final = 120.0
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

    started = time.monotonic()
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
            verdict, probes, llm_usage = _pipeline(store, workspaces, change, config, seed)
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
            probes=probes,
            verdict=verdict,
            duration_s=round(time.monotonic() - started, 3),
            llm=llm_usage,
        )
        log.info("verdict %s (%s)", verdict.verdict, verdict.rule_id)
    finally:
        detach_run_log(handler)
    store.track(RUN_LOG)
    sealed, digest = store.seal(manifest)
    return VerifyOutcome(sealed, digest, store.run_dir, config)


@dataclass
class _Context:
    """Everything the probes need once the head baseline is known."""

    adapter: Adapter
    head: Workspace
    base: Workspace
    collected: list[str]
    coverage: CoverageMap | None
    selection: Selection
    baseline: BaselineReport


def _pipeline(
    store: EvidenceStore,
    workspaces: WorkspaceManager,
    change: ChangeSet,
    config: Config,
    seed: bytes,
) -> tuple[Verdict, list[ProbeResult], LLMUsage]:
    """Section 6.3 steps 2-7: workspaces, baseline, probes (P5, P2, P1, P3, P4), verdict."""
    session, models, llm_note = build_session(config)
    llm = session is not None
    log.info("LLM: %s", llm_note)
    transcript = load_transcript(change.transcript_path) if change.transcript_path else None
    prepared = _prepare(store, workspaces, change, config)
    if isinstance(prepared, str):
        claims = _claim_audit(store, config, change, transcript, None)
        inp = DecisionInput(
            intent=change.intent, build_error=prepared, probes=claims, llm_enabled=llm
        )
        return decide(inp, config), claims, _usage(store, config, session, llm_note)

    ctx = prepared
    # P5 first: it needs only the transcript and the baseline (FR-IS-05: also on a red head).
    probes: list[ProbeResult] = _claim_audit(
        store, config, change, transcript, ctx.baseline.failing
    )
    early = early_verdict(
        DecisionInput(change.intent, baseline=ctx.baseline, probes=probes), config
    )
    if early is not None:
        # FR-IS-04/05: no proof is possible on a flaky or red head
        return early, probes, _usage(store, config, session, llm_note)

    repo = change.repo_root
    read_base = partial(git.show_file, repo, change.base_sha)
    read_head = partial(git.show_file, repo, change.head_snapshot_sha)

    def decision() -> DecisionInput:
        skipped = len(session.budget.skipped) if session is not None else 0
        return DecisionInput(
            change.intent,
            baseline=ctx.baseline,
            probes=probes,
            llm_enabled=llm,
            llm_skipped=skipped,
        )

    # P2 first: static and cheap (PRD 6.2, cheap first, expensive last).
    selected_files = sorted({t.partition("::")[0] for t in ctx.selection.tests})
    integrity = run_integrity(
        change,
        read_base=read_base,
        read_head=read_head,
        literal_test_files=selected_files,
        covering_by_file=_covering_by_file(change, ctx.coverage),
    )
    probes.append(integrity)
    store.write_json("probes/integrity.json", integrity)

    refuted = early_verdict(decision(), config)
    if refuted is not None and config.run.short_circuit:
        for name in ("red_green", "mutants", "blind_oracle"):
            if name != "blind_oracle" or name in config.enabled_probes:
                probes.append(ProbeResult(probe=name, status="skipped", signals=["SHORT_CIRCUIT"]))
        return refuted, probes, _usage(store, config, session, llm_note)

    pairs = changed_test_file_pairs(change, read_base, read_head)
    changed_tests = changed_test_ids(pairs)

    # P1 red-green. For test-only changes it does not apply (section 8): P3 covers them.
    if change.intent == "test_only":
        probes.append(
            ProbeResult(probe="red_green", status="skipped", signals=["RG_NOT_APPLICABLE"])
        )
    else:
        probes.append(
            _red_green(
                store,
                ctx,
                change=change,
                config=config,
                pairs=pairs,
                read_base=read_base,
                read_head=read_head,
            )
        )

    # P3 mutants.
    probes.append(
        _mutants(
            store,
            workspaces,
            ctx,
            change=change,
            config=config,
            changed_tests=changed_tests,
            seed=seed,
            llm=_llm_context(
                store, ctx, change=change, config=config, session=session, models=models
            ),
        )
    )

    # P4 blind oracle: the most expensive probe, last (PRD 6.2).
    if "blind_oracle" in config.enabled_probes:
        probes.append(
            _blind_oracle(
                store,
                ctx,
                change=change,
                config=config,
                session=session,
                models=models,
                transcript=transcript,
                selected_files=selected_files,
                read_base=read_base,
                read_head=read_head,
            )
        )
    return decide(decision(), config), probes, _usage(store, config, session, llm_note)


def _claim_audit(
    store: EvidenceStore,
    config: Config,
    change: ChangeSet,
    transcript: Transcript | None,
    baseline_failing: list[str] | None,
) -> list[ProbeResult]:
    """P5 (FR-CA): pure over the transcript and the baseline's failing tests."""
    if "claim_audit" not in config.enabled_probes:
        return []
    audit = run_claim_audit(
        transcript,
        transcript_given=change.transcript_path is not None,
        baseline_failing=baseline_failing,
    )
    if audit.result.status != "skipped":
        store.write_json("probes/claim_audit.json", audit.result)
    if audit.evidence:
        store.write_json("claims.json", audit.evidence)
    return [audit.result]


def _blind_oracle(
    store: EvidenceStore,
    ctx: _Context,
    *,
    change: ChangeSet,
    config: Config,
    session: LLMSession | None,
    models: dict[str, str],
    transcript: Transcript | None,
    selected_files: list[str],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
) -> ProbeResult:
    if session is None:
        return skipped(BO_NO_LLM).result
    excluded = list(config.paths.exclude)

    def sendable(path: str) -> bool:
        return may_send(path, excluded)

    interface = extract_interface(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=selected_files,
        sendable=sendable,
    )
    if interface.empty:
        return skipped(BO_NO_INTERFACE).result
    author = transcript.author_model if transcript is not None else None
    try:
        oracle_models, note = choose_oracle_models(
            config.llm.provider,
            config.llm.oracle_model,
            author,
            max(1, config.profile_spec.oracle_models),
        )
    except ModelNotConfiguredError:
        oracle_models = [models["mutant"]]
        note = f"oracle model {models['mutant']} (the mutant model)"
    log.info("blind oracle: %s", note)
    outcome = run_blind_oracle(
        session=session,
        models=oracle_models,
        model_note=note,
        judge_model=models["judge"],
        k=config.oracle.k,
        task_text=change.task_text,
        interface=interface,
        functions=changed_function_sources(change, read_head, sendable=sendable),
        adapter=ctx.adapter,
        head=ctx.head,
        base=ctx.base,
        run_id=store.run_id,
        junit_path=lambda name: store.path(f"oracle/{name}.xml"),
        timeout_s=min(config.impact.run_timeout_s, ORACLE_RUN_TIMEOUT_S),
    )
    store.write_text("oracle/interface.txt", interface.render() + "\n")
    if outcome.test_file is not None:
        store.write_text("oracle/test_oracle.py", outcome.test_file)
    store.write_json("oracle/oracle.json", outcome.record)
    for name in ("head-1", "head-2", "head-3", "base"):
        _track_existing(store, f"oracle/{name}.xml", f"oracle/{name}.log")
    store.write_json("probes/blind_oracle.json", outcome.result)
    return outcome.result


def _usage(store: EvidenceStore, config: Config, session: LLMSession | None, note: str) -> LLMUsage:
    if session is None:
        return LLMUsage(provider="none", note=note)
    calls = session.calls
    store.write_json("llm_calls.json", [vars(c) for c in calls])
    return LLMUsage(
        provider=config.llm.provider,
        note=note,
        models=sorted({c.model for c in calls}),
        calls=len(calls),
        ok=sum(1 for c in calls if c.ok),
        cached=sum(1 for c in calls if c.cached),
        tokens=session.budget.tokens,
        usd=round(session.budget.usd, 6),
        redactions=sum(c.redactions for c in calls),
        skipped=list(session.budget.skipped),
        errors=[f"{c.role}: {c.error}" for c in calls if not c.ok and c.error],
    )


def _llm_context(
    store: EvidenceStore,
    ctx: _Context,
    *,
    change: ChangeSet,
    config: Config,
    session: LLMSession | None,
    models: dict[str, str],
) -> tuple[LLMContext | None, set[CodeLiteral]]:
    """The LLM half of P3 plus the test literals FR-IN-07 and FR-MU-04 both use."""
    literals: set[CodeLiteral] = set()
    tests_by_file: dict[str, dict[str, str]] = {}
    for path in sorted({t.partition("::")[0] for t in ctx.selection.tests}):
        target = ctx.head.root / path
        analysed = (
            analyse_test_file(target.read_text(encoding="utf-8")) if target.is_file() else None
        )
        if analysed is not None:
            literals |= analysed.literals
            tests_by_file[path] = {k: fn.code for k, fn in analysed.tests.items()}
    if session is None:
        return None, literals

    def test_source(nodeids: Sequence[str]) -> str:
        parts = []
        for nodeid in nodeids:
            file, _, rest = nodeid.partition("::")
            code = tests_by_file.get(file, {}).get(rest.split("[", 1)[0])
            if code:
                parts.append(code.strip())
        return "\n\n".join(dict.fromkeys(parts))

    adapter = ctx.adapter
    fuzz = make_fuzzer(
        head_text=lambda p: _read_head(ctx, p),
        literals=literals,
        run_script=lambda script, args: adapter.run_script(ctx.head, script, args),
        script_dir=store.run_dir / "fuzz",
    )
    excluded = list(config.paths.exclude)
    return (
        LLMContext(
            session=session,
            mutant_model=models["mutant"],
            judge_model=models["judge"],
            task_text=change.task_text,
            sendable=lambda path: may_send(path, excluded),
            test_source=test_source,
            fuzz=fuzz,
        ),
        literals,
    )


def _red_green(
    store: EvidenceStore,
    ctx: _Context,
    *,
    change: ChangeSet,
    config: Config,
    pairs: list[TestFilePair],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
) -> ProbeResult:
    added, new_modules = _added_api(change, read_base, read_head)
    outcome = run_red_green(
        change=change,
        adapter=ctx.adapter,
        head=ctx.head,
        base=ctx.base,
        changed_tests=changed_test_ids(pairs),
        collected=ctx.collected,
        selection=ctx.selection,
        baseline=ctx.baseline,
        added_symbols=added,
        new_modules=new_modules,
        junit_path=lambda name: store.path(f"junit/red-green-{name}.xml"),
        timeout_s=config.impact.run_timeout_s,
    )
    for name in ("base-1", "base-2"):
        _track_existing(store, f"junit/red-green-{name}.xml", f"junit/red-green-{name}.log")
    store.write_json("probes/red_green.json", outcome.result)
    store.write_json(
        "red_green_cases.json",
        {
            "candidate_source": outcome.candidate_source,
            "transplanted": outcome.transplanted,
            "cases": [c.model_dump(mode="json") for c in outcome.cases],
        },
    )
    return outcome.result


def _mutants(
    store: EvidenceStore,
    workspaces: WorkspaceManager,
    ctx: _Context,
    *,
    change: ChangeSet,
    config: Config,
    changed_tests: list[str],
    seed: bytes,
    llm: tuple[LLMContext | None, set[CodeLiteral]],
) -> ProbeResult:
    def head_text(path: str) -> str:
        return _read_head(ctx, path)

    def workers(count: int) -> list[Workspace]:
        # FR-WS-03: one head worktree per worker, created before any mutant runs.
        return [workspaces.add(f"w{i}", change.head_snapshot_sha) for i in range(1, count + 1)]

    outcome = run_mutation_probe(
        change=change,
        config=config,
        adapter=ctx.adapter,
        workers=workers,
        head_text=head_text,
        coverage=ctx.coverage,
        selection=ctx.selection,
        baseline=ctx.baseline,
        changed_tests=changed_tests,
        seed=seed,
        junit_path=lambda name: store.path(f"mutants/{name}.xml"),
        timeout_for=lambda tests: mutant_timeout_s(ctx.baseline, tests),
        test_literals=llm[1],
        llm=llm[0],
    )
    for mutant in outcome.mutants:
        for name in (mutant.id, f"{mutant.id}-rerun"):
            _track_existing(store, f"mutants/{name}.xml", f"mutants/{name}.log")
    store.write_json(
        "mutants.json",
        {
            "filtered": outcome.filtered,
            "mutants": [m.model_dump(mode="json") for m in outcome.mutants],
        },
    )
    store.write_json("probes/mutants.json", outcome.result)
    return outcome.result


def _read_head(ctx: _Context, path: str) -> str:
    """Byte-exact head file text (no newline translation): mutant line ranges and original
    text must match what runs, including CRLF checkouts on Windows."""
    return (ctx.head.root / path).read_bytes().decode("utf-8", "replace")


def _covering_by_file(
    change: ChangeSet, coverage: CoverageMap | None
) -> dict[str, set[str]] | None:
    """Tests that execute any line of each changed source file (for FR-IN-06)."""
    if coverage is None:
        return None
    result: dict[str, set[str]] = {f.path: set() for f in change.files if f.role == "source"}
    for (path, _line), tests in coverage.items():
        if path in result:
            result[path] |= tests
    return result


def _added_api(
    change: ChangeSet,
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
) -> tuple[set[str], set[str]]:
    """Symbols and modules the change added (FR-AD-04 API_ABSENT)."""
    added: set[str] = set()
    new_modules: set[str] = set()
    for f in change.files:
        if f.role != "source" or f.status == "deleted" or not f.path.endswith(".py"):
            continue
        head_text = read_head(f.path) or ""
        base_text = None if f.status == "added" else read_base(f.old_path or f.path)
        added |= added_symbols(base_text, head_text)
        if f.status == "added":
            new_modules.add(module_name(f.path))
    return added, new_modules


def _prepare(
    store: EvidenceStore, workspaces: WorkspaceManager, change: ChangeSet, config: Config
) -> _Context | str:
    """Workspaces, stack detection, impact selection and baseline (6.3 steps 2-5).

    Returns the probe context, or a rule-1 (R-INC-BUILD) reason.
    """
    if NO_CODE_CHANGE in change.flags:
        return "NO_CODE_CHANGE: the change has no code to verify"  # FR-CR-07

    head = workspaces.add("head", change.head_snapshot_sha)
    base = workspaces.add("base", change.base_sha)
    adapters = detect_adapters(head.root, env_pass=config.env.pass_)
    if not adapters:
        return "no supported test runner detected (pytest in this version)"
    adapter = adapters[0]
    dropped = dropped_names(os.environ, scrub_env(os.environ, config.env.pass_))
    log.info("child env: dropped %d variables (names in run log)", len(dropped))
    log.debug("dropped env names: %s", ", ".join(dropped))

    prep = adapter.prepare(head)
    if not prep.ok:
        return f"head cannot prepare: {prep.message}"
    try:
        all_tests = adapter.list_tests(head)
    except CollectionError as exc:
        store.write_text("logs/collect-head.log", exc.output)
        return f"head cannot collect tests: {exc}"

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
        return "no selected tests for the changed lines"

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
    return _Context(
        adapter=adapter,
        head=head,
        base=base,
        collected=[t.nodeid for t in all_tests],
        coverage=coverage,
        selection=selection,
        baseline=report,
    )


def _track_existing(store: EvidenceStore, *paths: str) -> None:
    for rel in paths:
        if store.path(rel).is_file():
            store.track(rel)
