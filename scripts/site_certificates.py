"""Capture the certificates shown on the website (site/data/certificates/).

Four real runs on the same reported bug (py_ledger T1, a discount cap), rule-only, seed
00..00: the honest fix, the hard-coded fix, the partial fix, and a correct-looking change
whose session claimed "all tests pass" after hiding a failing exit code.

    uv run python scripts/site_certificates.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers import FIXTURE_REPOS, build_repo  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from counterproof.cli import app  # noqa: E402

OUT = ROOT / "site" / "data" / "certificates"
SEED = "00" * 16
RUNS = [
    ("honest", "Honest fix", "honest", None, None),
    ("hardcoded", "Hard-coded", "hardcoded", None, None),
    ("partial", "Partial fix", "partial", None, None),
    (
        "claim",
        "“Tests pass”",
        "honest",
        FIXTURE_REPOS / "py_ledger" / "c2" / "broken-cap",
        ROOT / "tests" / "fixtures" / "transcripts" / "c2" / "masked-green.jsonl",
    ),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cp-site-"))
    os.environ["COUNTERPROOF_CACHE_DIR"] = str(work / "cache")
    os.environ.setdefault("COUNTERPROOF_PYTHON", sys.executable)
    index = []
    for key, tab, variant, overlay, transcript in RUNS:
        repo = build_repo(work / key, task="T1-discount-cap", variant=variant, overlay=overlay)
        args = ["verify", "--format", "md", "--seed", SEED]
        if transcript is not None:
            args += ["--transcript", str(transcript)]
        cwd = Path.cwd()
        os.chdir(repo.path)
        try:
            result = CliRunner().invoke(app, args)
        finally:
            os.chdir(cwd)
        text = result.stdout
        # Machine-specific lines (run id, timing) do not belong on a public page.
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("Evidence: run"))
        (OUT / f"{key}.md").write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
        index.append({"key": key, "tab": tab})
        print(key, text.splitlines()[0])
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
