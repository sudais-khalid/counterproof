"""Claude Code Stop hook, coach mode (PRD section 13.1).

Checked against the Claude Code hooks reference (November 2026). Stop hook input (stdin
JSON): ``session_id``, ``transcript_path``, ``cwd``, ``hook_event_name`` (``"Stop"``),
``stop_hook_active`` (true while Claude is already continuing because of a stop hook),
``last_assistant_message`` (the final reply; the transcript file may lag behind it) and more
fields we ignore. Output: exit 0 with a JSON object on stdout. ``{"decision": "block",
"reason": ...}`` keeps Claude working and shows it the reason; ``{"systemMessage": ...}``
only informs the user. Claude Code itself caps consecutive stop-hook continuations.

Behaviour:
* ``stop_hook_active`` set: exit 0 immediately (no loops).
* Otherwise run ``counterproof verify`` (fast profile, coach mode, the session transcript)
  as a child process with a hard 90 second limit.
* REFUTED: ``decision: block`` with the coach hint (gap class and location only).
* WEAK or UNPROVEN: the same hint as ``hookSpecificOutput.additionalContext``, which the
  docs reserve for a hook "working as designed and giving Claude guidance": Claude keeps
  working, with the same loop protections, but the user sees no "hook error" notice.
* PROVEN or INCONCLUSIVE: a one-line system message; no change at all: silence.
* Anything unexpected (not a repo, timeout, crash): never block, say so to the user.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from counterproof import git
from counterproof.evidence.store import cache_root, runs_root
from counterproof.log import get_logger
from counterproof.models import RunManifest
from counterproof.report.bundle import load_bundle
from counterproof.report.coach import coach_hint, summary_line
from counterproof.workspace.links import unlink_all
from counterproof.workspace.sandbox_none import ProcessResult, run_process

log = get_logger("hook")

HOOK_TIMEOUT_S: Final = 90.0
BLOCKING: Final = frozenset({"WEAK", "UNPROVEN", "REFUTED"})
NO_CHANGE_RULE: Final = "R-INC-BUILD"

Runner = Callable[[list[str], Path, float], ProcessResult]


@dataclass(frozen=True)
class HookInput:
    cwd: Path
    transcript_path: Path | None
    stop_hook_active: bool
    last_assistant_message: str | None
    session_id: str | None


def parse_input(raw: str, *, default_cwd: Path) -> HookInput:
    """Tolerant: unknown fields are ignored, missing ones get safe defaults."""
    try:
        data: Any = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    transcript = data.get("transcript_path")
    cwd = data.get("cwd")
    message = data.get("last_assistant_message")
    session = data.get("session_id")
    return HookInput(
        cwd=Path(cwd) if isinstance(cwd, str) and cwd else default_cwd,
        transcript_path=Path(transcript).expanduser()
        if isinstance(transcript, str) and transcript
        else None,
        stop_hook_active=data.get("stop_hook_active") is True,
        last_assistant_message=message if isinstance(message, str) else None,
        session_id=session if isinstance(session, str) else None,
    )


def _default_runner(cmd: list[str], cwd: Path, timeout_s: float) -> ProcessResult:
    return run_process(cmd, cwd=cwd, env=dict(os.environ), timeout_s=timeout_s)


def verify_command(hook: HookInput, message_file: Path | None) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "counterproof",
        "verify",
        "--profile",
        "fast",
        "--mode",
        "coach",
        "--format",
        "json",
    ]
    if hook.transcript_path is not None and hook.transcript_path.is_file():
        cmd += ["--transcript", str(hook.transcript_path)]
    if message_file is not None:
        cmd += ["--final-message-file", str(message_file)]
    return cmd


def cleanup_stale_worktrees(repo: Path, cache: Path) -> int:
    """Remove worktrees a killed run left behind (only ours: under the runs directory)."""
    root = runs_root(cache).resolve()
    listing = git.try_git(["worktree", "list", "--porcelain"], repo) or ""
    removed = 0
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line.removeprefix("worktree ").strip())
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        unlink_all(path)
        git.try_git(["worktree", "remove", "--force", str(path)], repo)
        removed += 1
    git.try_git(["worktree", "prune"], repo)
    return removed


def _message(text: str) -> dict[str, Any]:
    return {"systemMessage": text}


def decide(manifest: RunManifest, run_dir: Path | None) -> dict[str, Any] | None:
    """The hook's JSON output for a finished run (``None``: print nothing)."""
    verdict = manifest.verdict
    if verdict.verdict == "INCONCLUSIVE" and any("NO_CODE_CHANGE" in r for r in verdict.reasons):
        return None
    if verdict.verdict not in BLOCKING:
        return _message(summary_line(manifest))
    mutants = load_bundle(run_dir).mutants if run_dir is not None else []
    hint = coach_hint(manifest, mutants)
    if verdict.verdict == "REFUTED":
        return {"decision": "block", "reason": hint}
    # WEAK / UNPROVEN: guidance, not an error. additionalContext keeps Claude working under
    # the same loop protections as a block, without a "hook error" notice to the user.
    return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": hint}}


def run_hook(
    raw_input: str,
    *,
    default_cwd: Path,
    runner: Runner = _default_runner,
    timeout_s: float = HOOK_TIMEOUT_S,
    cache: Path | None = None,
) -> dict[str, Any] | None:
    hook = parse_input(raw_input, default_cwd=default_cwd)
    if hook.stop_hook_active:
        return None  # PRD 13.1: never re-run while Claude is already continuing for us
    try:
        repo = git.repo_root(hook.cwd)
    except Exception:
        return None
    cache = cache if cache is not None else cache_root()
    message_file: Path | None = None
    if hook.last_assistant_message:
        cache.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - removed in finally
            "w", suffix=".txt", dir=cache, delete=False, encoding="utf-8"
        )
        with handle:
            handle.write(hook.last_assistant_message)
        message_file = Path(handle.name)
    try:
        proc = runner(verify_command(hook, message_file), repo, timeout_s)
    finally:
        if message_file is not None:
            message_file.unlink(missing_ok=True)
    if proc.timed_out:
        removed = cleanup_stale_worktrees(repo, cache)
        log.warning("verify timed out after %.0fs; removed %d worktree(s)", timeout_s, removed)
        return _message(f"Counterproof: no verdict within {timeout_s:.0f}s (not blocking).")
    try:
        manifest = RunManifest.model_validate_json(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return _message(f"Counterproof: could not verify ({tail[0][:200]}); not blocking.")
    run_dir = _run_dir(cache, manifest)
    return decide(manifest, run_dir)


def _run_dir(cache: Path, manifest: RunManifest) -> Path | None:
    matches = list(runs_root(cache).glob(f"*/{manifest.run_id}"))
    return matches[0] if matches else None


def main(stdin_text: str, env: Mapping[str, str] | None = None) -> str:
    """Entry point used by the CLI: returns what to print (possibly empty)."""
    env = os.environ if env is None else env
    default = Path(env.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    try:
        output = run_hook(stdin_text, default_cwd=default)
    except Exception as exc:
        log.debug("hook failed", exc_info=True)
        output = _message(f"Counterproof hook error ({type(exc).__name__}); not blocking.")
    return json.dumps(output) if output is not None else ""
