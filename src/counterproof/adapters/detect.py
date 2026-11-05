"""StackDetector: choose adapters from project manifests (PRD section 6.1).

v1 milestone M1 ships pytest only; Vitest and Jest join in M6. Monorepo ownership by the
nearest project root (FR-AD-06) arrives with the second adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from counterproof.adapters.pytest_adapter import PytestAdapter

MIN_CONFIDENCE: Final = 0.5


def detect_adapters(
    repo: Path,
    *,
    env_pass: Sequence[str] = (),
    source_env: Mapping[str, str] | None = None,
) -> list[PytestAdapter]:
    """Adapters whose confidence for ``repo`` is at least :data:`MIN_CONFIDENCE`."""
    candidates = [PytestAdapter(repo, env_pass=env_pass, source_env=source_env)]
    scored = [(a.detect(repo), a) for a in candidates]
    return [a for score, a in sorted(scored, key=lambda s: -s[0]) if score >= MIN_CONFIDENCE]
