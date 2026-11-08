#!/usr/bin/env bash
# Two-minute Counterproof demo, replayed deterministically for screen recording.
# Script and timings: docs/demo-script.md. Needs the dev environment (uv sync).
#
#   bash scripts/demo.sh            # pauses between scenes (press Enter)
#   DEMO_PAUSE=4 bash scripts/demo.sh   # or a fixed pause in seconds
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
WORK="$(mktemp -d)"
export COUNTERPROOF_CACHE_DIR="$WORK/cache" COUNTERPROOF_PYTHON="$PY"
SEED=00000000000000000000000000000000

pause() {
  if [ -n "${DEMO_PAUSE:-}" ]; then sleep "$DEMO_PAUSE"; else read -r -p "" _; fi
}
scene() { printf '\n\033[1;36m# %s\033[0m\n' "$1"; }
run() { printf '\033[1;32m$ %s\033[0m\n' "$*"; "$@" || true; }

build() {  # build <dir> <task> <variant> [overlay]
  "$PY" - "$@" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from tests.helpers import FIXTURE_REPOS, build_repo
dest, task, variant, *overlay = sys.argv[1:]
build_repo(Path(dest), task=task, variant=variant,
           overlay=FIXTURE_REPOS / "py_ledger" / overlay[0] if overlay else None)
PY
}

cd "$ROOT"
build "$WORK/hardcoded" T1-discount-cap hardcoded
build "$WORK/partial" T1-discount-cap partial
build "$WORK/claim" T1-discount-cap honest c2/broken-cap

scene "An agent fixed a discount bug and says: all tests pass. Do the tests prove it?"
cat tests/fixtures/repos/py_ledger/tasks/T1-discount-cap/task.md
pause

scene "1. Hard-coded fix: the code special-cases the test's own inputs"
(cd "$WORK/hardcoded" && run "$PY" -m counterproof verify --format md --seed "$SEED")
pause

scene "2. Partial fix: green tests, but a wrong version of the change would pass them too"
(cd "$WORK/partial" && run "$PY" -m counterproof verify --format md --seed "$SEED")
pause

scene "3. The same agent, coached in Claude Code: only the gap and its location, no answers"
(cd "$WORK/partial" && run "$PY" -m counterproof verify --mode coach --seed "$SEED")
pause

scene "4. 'Done, the test suite passes' - but the session hid the failing exit code"
(cd "$WORK/claim" && run "$PY" -m counterproof verify --seed "$SEED" \
  --transcript "$ROOT/tests/fixtures/transcripts/c2/masked-green.jsonl" --format md)
pause

scene "5. The evaluation: 40 seeded changes, against green tests and claim checking"
sed -n '/^## Headline/,/^## Configuration/p' eval/results/2026-11-08-c1.md | head -n 12
printf '\nRun it yourself: counterproof eval eval/corpora/c1\n'
