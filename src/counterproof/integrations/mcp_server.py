"""MCP server over stdio (PRD section 13.2), built on the official MCP Python SDK 2.x
(``mcp.server.MCPServer``; checked against mcp 2.3, November 2026).

Tools:
* ``verify_change(base?, head?, task?, profile?)``: runs a verification in the server's
  working directory and returns the verdict, run id and the coach hint.
* ``get_report(run_id, format)``: the certificate. Agents get the redacted Markdown view;
  JSON, HTML and SARIF (which carry mutant code) only when the human allowed it.
* ``explain_mutant(run_id, mutant_id)``: a description, never code, unless
  ``[mcp] reveal_mutants = true``.

``reveal_mutants`` is read from the configuration at the *base* commit, like every other
setting (PRD 15.3), so an agent cannot switch it on by editing ``counterproof.toml``.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Final

import anyio
from mcp.server import MCPServer

from counterproof import git
from counterproof.errors import CounterproofError
from counterproof.orchestrator import VerifyRequest, load_run_config, run_verify
from counterproof.report import render_json
from counterproof.report.bundle import RunBundle, find_run, load_bundle
from counterproof.report.coach import coach_hint, summary_line
from counterproof.report.html import render_html
from counterproof.report.markdown import render_markdown
from counterproof.report.sarif import render_sarif

PROFILES: Final = ("fast", "full", "paranoid")
FORMATS: Final = ("md", "json", "html", "sarif")


def reveal_allowed(cwd: Path) -> bool:
    try:
        repo = git.repo_root(cwd)
        config = load_run_config(repo, git.default_base(repo), "base", {})
    except CounterproofError:
        return False
    return config.mcp.reveal_mutants


def _summary(bundle: RunBundle) -> str:
    manifest = bundle.manifest
    verdict = manifest.verdict
    if verdict.verdict in {"PROVEN", "INCONCLUSIVE"}:
        text = summary_line(manifest)
    else:
        text = coach_hint(manifest, bundle.mutants)
    return f"{text}\nRun id: {manifest.run_id} (get_report for the certificate)."


def verify_change_sync(
    cwd: Path,
    base: str | None = None,
    head: str | None = None,
    task: str | None = None,
    profile: str | None = None,
) -> str:
    if profile is not None and profile not in PROFILES:
        return f"error: profile must be one of {', '.join(PROFILES)}"
    overrides = {"run": {"profile": profile}} if profile else {}
    try:
        outcome = run_verify(
            VerifyRequest(cwd=cwd, base=base, head=head, task_text=task, overrides=overrides)
        )
    except CounterproofError as exc:
        return f"error: {exc}"
    return _summary(load_bundle(outcome.run_dir))


def get_report_sync(cwd: Path, run_id: str, fmt: str = "md") -> str:
    if fmt not in FORMATS:
        return f"error: format must be one of {', '.join(FORMATS)}"
    try:
        bundle = load_bundle(find_run(run_id))
    except CounterproofError as exc:
        return f"error: {exc}"
    reveal = reveal_allowed(cwd)
    if fmt == "md":
        return render_markdown(bundle, redact=not reveal)
    if not reveal:
        return (
            f"error: --format {fmt} includes mutant code; a human must set "
            "[mcp] reveal_mutants = true in counterproof.toml. Use format 'md'."
        )
    renderers = {"json": render_json, "html": render_html, "sarif": render_sarif}
    return renderers[fmt](bundle)


def explain_mutant_sync(cwd: Path, run_id: str, mutant_id: str) -> str:
    try:
        bundle = load_bundle(find_run(run_id))
    except CounterproofError as exc:
        return f"error: {exc}"
    mutant = bundle.mutant(mutant_id)
    if mutant is None:
        known = ", ".join(m.id for m in bundle.mutants) or "none"
        return f"error: no mutant {mutant_id!r} in run {run_id} (known: {known})"
    lines = [
        f"Mutant {mutant.id}: {mutant.source} / {mutant.operator_or_class}, {mutant.status}",
        f"Location: {mutant.file}:{mutant.start_line}-{mutant.end_line}",
        f"Tests run against it: {len(mutant.covering_tests)}",
    ]
    if not reveal_allowed(cwd):
        lines.append("Code withheld (a human can set [mcp] reveal_mutants = true).")
        return "\n".join(lines)
    diff = difflib.unified_diff(
        mutant.original.splitlines(keepends=True),
        mutant.replacement.splitlines(keepends=True),
        fromfile=f"a/{mutant.file}",
        tofile=f"b/{mutant.file} ({mutant.id})",
    )
    lines += ["", "".join(diff).rstrip("\n")]
    if mutant.hint:
        lines.append(f"Missing test: {mutant.hint}")
    return "\n".join(lines)


def build_server(cwd: Path | None = None) -> MCPServer:
    root = cwd or Path.cwd()
    server = MCPServer(
        "counterproof",
        instructions=(
            "Counterproof checks whether the tests of a code change actually prove it. Call "
            "verify_change after editing code; act on the gap it names (add the missing "
            "test), then verify again."
        ),
    )

    @server.tool()
    async def verify_change(
        base: str | None = None,
        head: str | None = None,
        task: str | None = None,
        profile: str | None = None,
    ) -> str:
        """Verify the current change (default: working tree against its merge base).

        Returns the verdict, the run id and, unless the change is proven, what kind of test
        is missing and where.
        """
        return await anyio.to_thread.run_sync(
            lambda: verify_change_sync(root, base, head, task, profile)
        )

    @server.tool()
    async def get_report(run_id: str, format: str = "md") -> str:
        """The certificate of a run: md (default), or json/html/sarif when allowed."""
        return await anyio.to_thread.run_sync(lambda: get_report_sync(root, run_id, format))

    @server.tool()
    async def explain_mutant(run_id: str, mutant_id: str) -> str:
        """What one mutant of a run is, where it is and whether it survived."""
        return await anyio.to_thread.run_sync(lambda: explain_mutant_sync(root, run_id, mutant_id))

    return server


def serve() -> None:
    build_server().run("stdio")
