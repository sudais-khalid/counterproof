"""Command-line interface (PRD section 11).

Exit codes: 0 PROVEN, 10 WEAK, 20 UNPROVEN, 30 REFUTED, 40 INCONCLUSIVE, 2 usage error,
1 internal error.
"""

from __future__ import annotations

import contextlib
import difflib
import enum
import signal
import sys
from collections.abc import Callable, Iterator
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, NoReturn, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from counterproof import __version__, git
from counterproof import doctor as doctor_mod
from counterproof.adapters.detect import detect_adapters
from counterproof.config import CONFIG_FILENAME
from counterproof.errors import CounterproofError, UsageError
from counterproof.evidence.store import gc_runs, parse_duration
from counterproof.log import get_logger, setup_logging
from counterproof.models import EXIT_INTERNAL_ERROR, EXIT_USAGE_ERROR, WORKTREE
from counterproof.orchestrator import VerifyOutcome, VerifyRequest, run_verify
from counterproof.probes.blind_oracle import export as oracle_export
from counterproof.report import RunBundle, find_run, load_bundle, render_json, render_markdown
from counterproof.report.terminal import header, render_terminal
from counterproof.verdict.engine import exit_code_for

T = TypeVar("T")

app = typer.Typer(
    name="counterproof",
    help="Decide whether an AI-written change is actually proven by its tests.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
hook_app = typer.Typer(help="Agent integration hooks.", no_args_is_help=True)
app.add_typer(hook_app, name="hook")

out_console = Console()
# soft_wrap on every message: never hard-wrap paths or errors (they are grepped and parsed).
err_console = Console(stderr=True)
log = get_logger("cli")


class IntentOpt(enum.StrEnum):
    bugfix = "bugfix"
    feature = "feature"
    refactor = "refactor"
    test_only = "test_only"


class ProfileOpt(enum.StrEnum):
    fast = "fast"
    full = "full"
    paranoid = "paranoid"


class ModeOpt(enum.StrEnum):
    audit = "audit"
    coach = "coach"


class ProviderOpt(enum.StrEnum):
    anthropic = "anthropic"
    openai = "openai"
    deepseek = "deepseek"
    ollama = "ollama"
    none = "none"


class SandboxOpt(enum.StrEnum):
    none = "none"
    docker = "docker"


class FormatOpt(enum.StrEnum):
    text = "text"
    md = "md"
    json = "json"
    html = "html"
    sarif = "sarif"


class ConfigFromOpt(enum.StrEnum):
    base = "base"
    worktree = "worktree"


class FailOnOpt(enum.StrEnum):
    weak = "weak"
    unproven = "unproven"
    refuted = "refuted"


class ReportFormatOpt(enum.StrEnum):
    md = "md"
    html = "html"
    json = "json"
    sarif = "sarif"


def _fail(message: str, code: int) -> NoReturn:
    err_console.print(f"[bold red]error:[/] {message}", highlight=False, soft_wrap=True)
    raise typer.Exit(code)


def _guard(fn: Callable[[], T]) -> T:
    """Map expected errors to exit codes; anything else is an internal error (exit 1)."""
    try:
        return fn()
    except typer.Exit:
        raise
    except UsageError as exc:
        _fail(str(exc), EXIT_USAGE_ERROR)
    except CounterproofError as exc:
        _fail(str(exc), EXIT_INTERNAL_ERROR)
    except Exception as exc:
        log.debug("internal error", exc_info=True)
        _fail(f"internal error: {type(exc).__name__}: {exc}", EXIT_INTERNAL_ERROR)


@contextlib.contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """FR-WS-07: turn SIGTERM into KeyboardInterrupt so worktree cleanup still runs."""

    def _raise(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:  # not in the main thread (e.g. some test runners)
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _not_implemented(command: str, milestone: str) -> NoReturn:
    _fail(f"`counterproof {command}` is not implemented yet (planned for {milestone})", 1)


def _version_callback(value: bool) -> None:
    if value:
        out_console.print(f"counterproof {__version__}", highlight=False, soft_wrap=True)
        raise typer.Exit(0)


@app.callback()
def _root(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="Repeat for more detail.")
    ] = 0,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    setup_logging(verbose, err_console)


@app.command()
def verify(
    base: Annotated[str | None, typer.Option(help="Base ref (default: HEAD).")] = None,
    head: Annotated[
        str | None, typer.Option(help=f"Head ref or {WORKTREE} (default: {WORKTREE}).")
    ] = None,
    task: Annotated[str | None, typer.Option(help="Task description text.")] = None,
    task_file: Annotated[
        Path | None, typer.Option(help="File containing the task description.")
    ] = None,
    intent: Annotated[IntentOpt | None, typer.Option(help="Override intent inference.")] = None,
    profile: Annotated[ProfileOpt | None, typer.Option(help="Run profile.")] = None,
    mode: Annotated[ModeOpt | None, typer.Option(help="audit or coach.")] = None,
    provider: Annotated[ProviderOpt | None, typer.Option(help="LLM provider.")] = None,
    budget_usd: Annotated[float | None, typer.Option(min=0.0, help="LLM spend cap.")] = None,
    sandbox: Annotated[SandboxOpt | None, typer.Option(help="Sandbox driver.")] = None,
    workers: Annotated[int | None, typer.Option(min=1, help="Parallel mutant workers.")] = None,
    seed: Annotated[str | None, typer.Option(help="Hex seed (reproduce a run).")] = None,
    transcript: Annotated[
        Path | None, typer.Option(help="Agent session transcript (JSONL).")
    ] = None,
    fmt: Annotated[FormatOpt, typer.Option("--format", help="Output format.")] = FormatOpt.text,
    out: Annotated[Path | None, typer.Option(help="Write output to this file.")] = None,
    keep: Annotated[bool, typer.Option(help="Keep worktrees after the run.")] = False,
    no_short_circuit: Annotated[
        bool, typer.Option(help="Run every probe even after an early verdict.")
    ] = False,
    allow_install: Annotated[
        bool, typer.Option(help="Allow adapters to install packages (FR-AD-05).")
    ] = False,
    config_from: Annotated[
        ConfigFromOpt,
        typer.Option(help="Read counterproof.toml from the base commit or the working tree."),
    ] = ConfigFromOpt.base,
    fail_on: Annotated[
        FailOnOpt | None,
        typer.Option(help="Exit non-zero only for this verdict or worse (CI gating)."),
    ] = None,
    final_message_file: Annotated[
        Path | None,
        typer.Option(help="The agent's final message (used by the Claude Code hook).", hidden=True),
    ] = None,
) -> None:
    """Verify the current change and write a sealed evidence bundle."""
    if task is not None and task_file is not None:
        _fail("--task and --task-file are mutually exclusive", EXIT_USAGE_ERROR)
    overrides: dict[str, Any] = {}
    run_over = {
        "profile": profile.value if profile else None,
        "mode": mode.value if mode else None,
        "sandbox": sandbox.value if sandbox else None,
        "workers": workers,
        "short_circuit": False if no_short_circuit else None,
    }
    llm_over = {"provider": provider.value if provider else None, "budget_usd": budget_usd}
    for section, values in (("run", run_over), ("llm", llm_over)):
        cleaned = {k: v for k, v in values.items() if v is not None}
        if cleaned:
            overrides[section] = cleaned
    if allow_install:
        # FR-AD-05: accepted for forward compatibility; no adapter installs anything yet.
        log.warning("--allow-install: package installation is not implemented; nothing installed")

    def _run() -> VerifyOutcome:
        task_file_text = None
        if task_file is not None:
            try:
                task_file_text = task_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise UsageError(f"cannot read --task-file {task_file}: {exc}") from exc
        final_message = None
        if final_message_file is not None:
            try:
                final_message = final_message_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise UsageError(f"cannot read --final-message-file: {exc}") from exc
        request = VerifyRequest(
            cwd=Path.cwd(),
            base=base,
            head=head,
            final_message=final_message,
            task_text=task,
            task_file_text=task_file_text,
            intent=intent.value if intent else None,
            transcript=transcript,
            seed_hex=seed,
            overrides=overrides,
            keep=keep,
            config_from=config_from.value,
        )
        with _sigterm_as_interrupt():
            return run_verify(request)

    outcome = _guard(_run)
    bundle = _guard(lambda: load_bundle(outcome.run_dir))
    _emit(bundle, fmt.value, out)
    verdict = outcome.manifest.verdict
    if fail_on is not None and verdict.verdict == "INCONCLUSIVE":
        err_console.print(
            "warning: INCONCLUSIVE does not fail --fail-on", highlight=False, soft_wrap=True
        )
    raise typer.Exit(exit_code_for(verdict, fail_on.value if fail_on else None))


def _emit(bundle: RunBundle, fmt: str, out: Path | None) -> None:
    """Write a rendered run to ``out`` or stdout (Rich when stdout is a terminal).

    In coach mode the text view is the coach hint only: no mutant code (PRD 5.3).
    """
    coach = bundle.config.get("run", {}).get("mode") == "coach"
    if fmt == "text" and coach:
        from counterproof.report.coach import coach_hint, summary_line  # noqa: PLC0415

        verdict = bundle.manifest.verdict.verdict
        hint = (
            summary_line(bundle.manifest)
            if verdict in {"PROVEN", "INCONCLUSIVE"}
            else coach_hint(bundle.manifest, bundle.mutants)
        )
        if out is not None:
            _guard(lambda: out.write_text(hint + "\n", encoding="utf-8"))
        else:
            sys.stdout.write(hint + "\n")
        return
    if fmt == "text" and out is None and sys.stdout.isatty():
        render_terminal(bundle, out_console)
        return
    if fmt == "json":
        rendered = render_json(bundle)
    elif fmt == "html":
        from counterproof.report.html import render_html  # noqa: PLC0415

        rendered = render_html(bundle)
    elif fmt == "sarif":
        from counterproof.report.sarif import render_sarif  # noqa: PLC0415

        rendered = render_sarif(bundle)
    elif fmt == "md":
        rendered = render_markdown(bundle)
    else:
        rendered = (
            f"{header(bundle)}\n\n{render_markdown(bundle)}\nRun directory: {bundle.run_dir}\n"
        )
    if out is not None:
        target = out
        _guard(lambda: target.write_text(rendered, encoding="utf-8"))
    else:
        sys.stdout.write(rendered)
        sys.stdout.flush()


@app.command()
def doctor() -> None:
    """Check git, test runners, providers and the run directory."""
    checks = _guard(doctor_mod.run_checks)
    styles = {"ok": "green", "info": "cyan", "warn": "yellow", "fail": "bold red"}
    table = Table(title="counterproof doctor", show_lines=False)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for check in checks:
        table.add_row(check.name, f"[{styles[check.status]}]{check.status}[/]", check.detail)
    out_console.print(table)
    if not doctor_mod.passed(checks):
        out_console.print("[bold red]doctor: required checks failed[/]")
        raise typer.Exit(1)
    out_console.print("[green]doctor: all required checks passed[/]")


@app.command()
def gc(
    older_than: Annotated[str, typer.Option(help="Age threshold, e.g. 7d, 12h.")] = "7d",
    dry_run: Annotated[bool, typer.Option(help="List runs without deleting.")] = False,
) -> None:
    """Remove stale run directories (FR-WS-07)."""
    try:
        age = parse_duration(older_than)
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE_ERROR)
    removed = _guard(lambda: gc_runs(age, dry_run=dry_run))
    verb = "would remove" if dry_run else "removed"
    for path in removed:
        out_console.print(f"{verb} {path}", highlight=False, soft_wrap=True)
    out_console.print(
        f"gc: {verb} {len(removed)} run(s) older than {older_than}", highlight=False, soft_wrap=True
    )


@app.command()
def report(
    run_id: Annotated[str, typer.Argument()],
    fmt: Annotated[ReportFormatOpt, typer.Option("--format")] = ReportFormatOpt.md,
    out: Annotated[Path | None, typer.Option(help="Write output to this file.")] = None,
) -> None:
    """Render a stored run."""
    bundle = _guard(lambda: load_bundle(find_run(run_id)))
    _emit(bundle, fmt.value, out)


@app.command()
def explain(
    run_id: Annotated[str, typer.Argument()],
    mutant_id: Annotated[str, typer.Argument()],
) -> None:
    """Show a mutant's diff, covering tests and hint (audit mode: for humans only)."""
    bundle = _guard(lambda: load_bundle(find_run(run_id)))
    mutant = bundle.mutant(mutant_id)
    if mutant is None:
        known = ", ".join(m.id for m in bundle.mutants) or "none"
        _fail(f"no mutant {mutant_id!r} in run {run_id} (known: {known})", EXIT_USAGE_ERROR)
    diff = difflib.unified_diff(
        mutant.original.splitlines(keepends=True),
        mutant.replacement.splitlines(keepends=True),
        fromfile=f"a/{mutant.file}:{mutant.start_line}",
        tofile=f"b/{mutant.file}:{mutant.start_line} ({mutant.id})",
    )
    lines = [
        f"Mutant {mutant.id} ({mutant.source} / {mutant.operator_or_class}): {mutant.status}",
        f"Location: {mutant.file}:{mutant.start_line}-{mutant.end_line}",
        "",
        "".join(diff).rstrip("\n"),
        "",
        f"Tests run: {len(mutant.covering_tests)}",
        *[f"  {t}" for t in mutant.covering_tests],
    ]
    if mutant.killed_by:
        lines += ["Killed by:", *[f"  {t}" for t in mutant.killed_by]]
    if mutant.hint:
        lines.append(f"Missing test: {mutant.hint}")
    sys.stdout.write("\n".join(lines) + "\n")


@app.command("export-oracle")
def export_oracle(
    run_id: Annotated[str, typer.Argument()],
    accept: Annotated[list[str] | None, typer.Option(help="Oracle test to accept.")] = None,
    dest: Annotated[Path | None, typer.Option(help="Destination directory.")] = None,
) -> None:
    """Copy accepted blind-oracle tests into the repository (FR-BO-06).

    Without --accept, lists the run's oracle tests. --accept all takes every usable test.
    """

    def _run() -> list[str]:
        record = oracle_export.load_record(find_run(run_id), run_id)
        if not accept:
            return [
                *oracle_export.describe(record),
                "Accept tests with --accept NAME (repeatable) or --accept all.",
            ]
        target_dir = dest if dest is not None else git.repo_root(Path.cwd()) / record["test_dir"]
        target, names = oracle_export.export(record, run_id, accept, target_dir)
        return [f"wrote {len(names)} oracle test(s) to {target}"]

    for line in _guard(_run):
        out_console.print(line, highlight=False, soft_wrap=True, markup=False)


@hook_app.command("claude-stop")
def hook_claude_stop() -> None:
    """Claude Code Stop hook (coach mode); reads the hook JSON on stdin (PRD 13.1)."""
    from counterproof.integrations import claude_hook  # noqa: PLC0415

    output = claude_hook.main(sys.stdin.read())
    if output:
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
    raise typer.Exit(0)


@hook_app.command("pre-push")
def hook_pre_push(
    remote: Annotated[str | None, typer.Argument(help="Remote name (from git).")] = None,
    url: Annotated[str | None, typer.Argument(help="Remote URL (from git).")] = None,
    fail_on: Annotated[
        FailOnOpt, typer.Option(help="Block the push at this verdict or worse.")
    ] = FailOnOpt.refuted,
) -> None:
    """git pre-push hook: verify each pushed commit range with the fast profile (PRD 13.4)."""
    from counterproof.integrations.git_hook import parse_push_lines  # noqa: PLC0415

    refs = [r for r in parse_push_lines(sys.stdin.read()) if not r.deleted]
    worst = 0
    for ref in refs:

        def _run(
            head: str = ref.local_sha, base: str | None = None if ref.new_branch else ref.remote_sha
        ) -> VerifyOutcome:
            request = VerifyRequest(
                cwd=Path.cwd(), base=base, head=head, overrides={"run": {"profile": "fast"}}
            )
            with _sigterm_as_interrupt():
                return run_verify(request)

        outcome = _guard(_run)
        verdict = outcome.manifest.verdict
        code = exit_code_for(verdict, fail_on.value)
        err_console.print(
            f"counterproof: {ref.local_ref} {verdict.verdict} ({verdict.rule_id}), "
            f"run {outcome.manifest.run_id}",
            highlight=False,
            soft_wrap=True,
        )
        worst = max(worst, code)
    if worst:
        err_console.print(
            f"counterproof: push blocked (--fail-on {fail_on.value}); "
            "see `counterproof report <run>`",
            highlight=False,
            soft_wrap=True,
        )
    raise typer.Exit(1 if worst else 0)


@app.command()
def mcp() -> None:
    """Run the stdio MCP server (PRD 13.2)."""
    from counterproof.integrations.mcp_server import serve  # noqa: PLC0415

    serve()


@app.command()
def init(
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
    claude: Annotated[
        bool,
        typer.Option("--claude", help="Add the Claude Code Stop hook (.claude/settings.json)."),
    ] = False,
    git_hook: Annotated[
        bool, typer.Option("--git-hook", help="Install a git pre-push hook.")
    ] = False,
    fail_on: Annotated[
        FailOnOpt, typer.Option(help="Verdict that blocks a push (--git-hook).")
    ] = FailOnOpt.refuted,
) -> None:
    """Write counterproof.toml for the detected stack, or install agent and git hooks."""
    if claude or git_hook:
        from counterproof.integrations.git_hook import (  # noqa: PLC0415
            install_claude_hook,
            install_pre_push,
        )

        def _install() -> list[str]:
            repo = git.repo_root(Path.cwd())
            done: list[str] = []
            if claude:
                path, changed = install_claude_hook(repo)
                done.append(
                    f"{'added the Stop hook to' if changed else 'Stop hook already in'} {path}"
                )
            if git_hook:
                hook = install_pre_push(repo, fail_on=fail_on.value, force=force)
                done.append(f"installed {hook} (blocks on {fail_on.value})")
            return done

        for line in _guard(_install):
            out_console.print(line, highlight=False, soft_wrap=True)
        return

    def _run() -> tuple[Path, str]:
        repo = git.repo_root(Path.cwd())
        target = repo / CONFIG_FILENAME
        if target.exists() and not force:
            raise UsageError(f"{target} already exists; use --force to overwrite")
        names = ", ".join(a.name for a in detect_adapters(repo)) or "none detected"
        template = resources.files("counterproof").joinpath("data/counterproof.toml")
        body = template.read_text(encoding="utf-8")
        target.write_text(
            f"# Generated by `counterproof init`. Detected test runners: {names}\n{body}",
            encoding="utf-8",
        )
        return target, names

    target, names = _guard(_run)
    out_console.print(f"wrote {target} (detected: {names})", highlight=False, soft_wrap=True)


@app.command("eval")
def eval_cmd(
    corpus_dir: Annotated[Path, typer.Argument()],
    baseline: Annotated[str | None, typer.Option(help="claims | mutmut | stryker")] = None,
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Run Counterproof over an evaluation corpus."""
    _not_implemented("eval", "M8")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
