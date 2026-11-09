"""Build the seeded hard-coded fix (py_ledger T1) as a git repository for the action's CI job.

    uv run python scripts/action_fixture.py DEST >> "$GITHUB_ENV"

Prints FIXTURE_BASE and FIXTURE_HEAD (commit shas) in GITHUB_ENV format.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.helpers import build_repo


def main() -> int:
    repo = build_repo(Path(sys.argv[1]), task="T1-discount-cap", variant="hardcoded")
    print(f"FIXTURE_BASE={repo.base_sha}")
    print(f"FIXTURE_HEAD={repo.head_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
