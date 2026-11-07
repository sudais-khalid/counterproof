"""StackDetector: choose adapters from project manifests (PRD section 6.1, FR-AD-06).

Every adapter scores the repository; those at or above :data:`MIN_CONFIDENCE` are active.
A run uses the adapter that owns the changed code: Python files belong to pytest, TS/JS
files to Vitest or Jest. Projects in subdirectories of a monorepo (a nested
``package.json`` or ``pyproject.toml``) are not run separately yet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from counterproof.adapters.base import Adapter
from counterproof.adapters.jest_adapter import JestAdapter
from counterproof.adapters.pytest_adapter import PytestAdapter
from counterproof.adapters.vitest_adapter import VitestAdapter
from counterproof.models import ChangeSet
from counterproof.probes import lang

MIN_CONFIDENCE: Final = 0.5
_LANGUAGE: Final = {"pytest": "python", "vitest": "typescript", "jest": "typescript"}


def detect_adapters(
    repo: Path,
    *,
    env_pass: Sequence[str] = (),
    source_env: Mapping[str, str] | None = None,
    deps_root: Path | None = None,
) -> list[Adapter]:
    """Adapters whose confidence for ``repo`` is at least :data:`MIN_CONFIDENCE`.

    ``repo`` is the tree to inspect (the head worktree); ``deps_root`` is where untracked
    dependencies live (the user's repository: its ``.venv`` and ``node_modules``).
    """
    deps = deps_root or repo
    candidates: list[Adapter] = [
        PytestAdapter(deps, env_pass=env_pass, source_env=source_env),
        VitestAdapter(deps, env_pass=env_pass, source_env=source_env),
        JestAdapter(deps, env_pass=env_pass, source_env=source_env),
    ]
    scored = [(a.detect(repo), i, a) for i, a in enumerate(candidates)]
    return [
        a for score, _, a in sorted(scored, key=lambda s: (-s[0], s[1])) if score >= MIN_CONFIDENCE
    ]


def owner_language(path: str) -> str | None:
    if lang.is_python(path):
        return "python"
    if lang.is_typescript(path):
        return "typescript"
    return None


def choose_adapter(adapters: Sequence[Adapter], change: ChangeSet) -> Adapter | None:
    """FR-AD-06: the active adapter whose language owns most of the changed code files."""
    if not adapters:
        return None
    counts: dict[str, int] = {}
    for f in change.files:
        language = owner_language(f.path)
        if language and f.role in {"source", "test"}:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        return adapters[0]
    ranked = sorted(adapters, key=lambda a: -counts.get(_LANGUAGE.get(a.name, ""), 0))
    best = ranked[0]
    return best if counts.get(_LANGUAGE.get(best.name, ""), 0) else adapters[0]
