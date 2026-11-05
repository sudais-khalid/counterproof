"""Terminal rendering: a one-line verdict header, then the certificate rendered by Rich."""

from __future__ import annotations

from typing import Final

from rich.console import Console
from rich.markdown import Markdown

from counterproof.report.bundle import RunBundle
from counterproof.report.markdown import render_markdown

STYLES: Final = {
    "PROVEN": "bold green",
    "WEAK": "bold yellow",
    "UNPROVEN": "bold red",
    "REFUTED": "bold white on red",
    "INCONCLUSIVE": "bold cyan",
}


def header(bundle: RunBundle) -> str:
    verdict = bundle.manifest.verdict
    return f"Counterproof verdict: {verdict.verdict} (rule {verdict.rule_id})"


def render_terminal(bundle: RunBundle, console: Console) -> None:
    verdict = bundle.manifest.verdict
    console.print(header(bundle), style=STYLES[verdict.verdict], highlight=False)
    console.print(Markdown(render_markdown(bundle)))
    console.print(f"Run directory: {bundle.run_dir}", highlight=False)
