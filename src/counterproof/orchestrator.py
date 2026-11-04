"""Run orchestration (PRD section 6.3).

M0 scope: resolve the repository and refs, load configuration, create the run directory,
derive the per-run seed, and seal a manifest. Change resolution and probes arrive in later
milestones, so the verdict is an explicit ``INCONCLUSIVE / NOT_IMPLEMENTED``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from counterproof import __version__, git
from counterproof.config import CONFIG_FILENAME, Config, load_config
from counterproof.errors import UsageError
from counterproof.evidence.seed import (
    SECRET_FILENAME,
    derive_seed,
    load_or_create_secret,
    parse_seed_hex,
    seed_commitment,
)
from counterproof.evidence.store import EvidenceStore, cache_root, runs_root
from counterproof.log import attach_run_log, detach_run_log, get_logger
from counterproof.models import WORKTREE, ChangeSet, Intent, RunManifest, Verdict

log = get_logger("orchestrator")

RUN_LOG = "run.log"
SEED_FILE = "seed"


@dataclass(frozen=True)
class VerifyRequest:
    cwd: Path
    base: str | None = None
    head: str | None = None
    task_text: str | None = None
    intent: Intent | None = None
    transcript: Path | None = None
    seed_hex: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifyOutcome:
    manifest: RunManifest
    manifest_sha256: str
    run_dir: Path
    config: Config


def run_verify(req: VerifyRequest, *, cache: Path | None = None) -> VerifyOutcome:
    cache = cache if cache is not None else cache_root()
    repo = git.repo_root(req.cwd)
    # Reading the config from the base commit (`--config-from base`, PRD 15.3) lands in M1.
    config = load_config(repo / CONFIG_FILENAME, req.overrides)

    # FR-CR-01 (partial): explicit refs are validated; merge-base defaulting lands in M1.
    base_sha = git.resolve_commit(repo, req.base or "HEAD")
    if req.head is None or req.head == WORKTREE:
        head_ref, head_snapshot_sha = WORKTREE, ""
    else:
        head_ref = git.resolve_commit(repo, req.head)
        head_snapshot_sha = head_ref

    if req.transcript is not None and not req.transcript.is_file():
        raise UsageError(f"transcript not found: {req.transcript}")

    store = EvidenceStore.create(repo, runs_dir=runs_root(cache))
    handler = attach_run_log(store.path(RUN_LOG))
    try:
        log.info("run %s for %s (base %s, head %s)", store.run_id, repo, base_sha, head_ref)
        seed = (
            parse_seed_hex(req.seed_hex)
            if req.seed_hex
            else derive_seed(load_or_create_secret(cache / SECRET_FILENAME), store.run_id)
        )
        # The seed stays in the user cache and is never listed as an artefact.
        store.write_text(SEED_FILE, seed.hex(), artefact=False)
        store.write_json("config.json", config.to_toml_dict())

        change = ChangeSet(
            repo_root=repo,
            base_sha=base_sha,
            head_ref=head_ref,
            head_snapshot_sha=head_snapshot_sha,
            intent=req.intent or "non_code",
            intent_source="user" if req.intent else "rule",
            task_text=req.task_text,
            transcript_path=req.transcript.resolve() if req.transcript else None,
        )
        store.write_json("change.json", change)

        verdict = Verdict.make(
            "INCONCLUSIVE",
            "NOT_IMPLEMENTED",
            reasons=["M0 skeleton: change resolution and probes are not implemented yet"],
            confidence_note="No probes ran; this run only records inputs.",
        )
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
