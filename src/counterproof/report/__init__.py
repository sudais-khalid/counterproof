"""Reporters: every format renders the same sealed run (PRD section 14)."""

from __future__ import annotations

from counterproof.evidence.hashing import pretty_json
from counterproof.report.bundle import RunBundle, find_run, load_bundle
from counterproof.report.markdown import render_markdown


def render_json(bundle: RunBundle) -> str:
    """The full manifest, for research and dashboards."""
    return pretty_json(bundle.manifest.model_dump(mode="json")).decode("utf-8")


__all__ = ["RunBundle", "find_run", "load_bundle", "render_json", "render_markdown"]
