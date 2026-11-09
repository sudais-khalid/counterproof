"""Record the observed golden verdicts of the extended C1 tier (tasks T6 to T20).

Every extended variant is verified exactly as the golden tests do it (seed 00..00, full
profile): rule-only, and with the recorded DeepSeek answers replayed. The observed verdict,
rule and the evidence behind it (kill ratio, findings) are written into the variant's entry
in expectations.yaml as ``provider_none`` / ``provider_llm`` with a ``_why``. Only entries
that have no observation yet are filled; an existing observation is never changed.

    uv run python tests/golden/observe.py [--repo py_ledger|ts_cart] [--jobs 3]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.helpers import EXPECTATIONS, TESTS_DIR, VARIANTS, build_repo, load_expectations  # noqa: E402

SEED = "00" * 16


def _why(manifest: Any) -> str:
    parts = []
    for probe in manifest.probes:
        metrics = probe.metrics
        if probe.probe == "mutants" and "valid" in metrics:
            parts.append(f"{int(metrics.get('killed', 0))}/{int(metrics['valid'])} mutants killed")
        if probe.probe == "integrity" and probe.findings:
            parts.append(", ".join(sorted({f.rule_id for f in probe.findings})))
        if probe.probe == "blind_oracle" and metrics.get("likely_bug"):
            parts.append(f"oracle: {int(metrics['likely_bug'])} likely bug(s)")
    if not parts and manifest.verdict.reasons:
        parts.append(re.sub(r"^\[[\w-]+\]\s*", "", manifest.verdict.reasons[0])[:120])
    return "; ".join(parts) or manifest.verdict.rule_id


def observe(repo_name: str, task: str, variant: str, llm: bool) -> tuple[str, str, str]:
    """``(verdict, rule_id, why)`` in a fresh cache, isolated from other runs (a subprocess
    each, so parallel observations never share a working directory)."""
    from counterproof.models import RunManifest  # noqa: PLC0415

    work = Path(tempfile.mkdtemp(prefix="cp-observe-"))
    repo = build_repo(work / "repo", repo_name, task, variant).path
    env = {
        **os.environ,
        "COUNTERPROOF_CACHE_DIR": str(work / "cache"),
        "COUNTERPROOF_PYTHON": sys.executable,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    env.pop("COUNTERPROOF_LLM_CASSETTES", None)
    env.pop("COUNTERPROOF_LLM_MODE", None)
    args = ["verify", "--format", "json", "--seed", SEED]
    if llm:
        env["COUNTERPROOF_LLM_CASSETTES"] = str(TESTS_DIR / "cassettes" / repo_name)
        env["COUNTERPROOF_LLM_MODE"] = "replay"
        args += ["--provider", "deepseek"]
    result = subprocess.run(
        [sys.executable, "-m", "counterproof", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    manifest = RunManifest.model_validate_json(result.stdout)
    if llm and manifest.llm is not None and manifest.llm.errors:
        raise SystemExit(f"{repo_name}/{task}/{variant}: missing cassette {manifest.llm.errors}")
    return manifest.verdict.verdict, manifest.verdict.rule_id, _why(manifest)


def _entry(verdict: str, rule: str) -> str:
    return f"{{verdict: {verdict}, rule_id: {rule}}}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=["py_ledger", "ts_cart"], action="append")
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    expectations = load_expectations()["repos"]
    jobs: list[tuple[str, str, str, bool]] = []
    for repo_name in args.repo or ["py_ledger", "ts_cart"]:
        for task, spec in expectations[repo_name]["tasks"].items():
            if spec.get("tier") != "extended":
                continue
            for variant in VARIANTS:
                have = spec["variants"][variant]
                for llm, key in ((False, "provider_none"), (True, "provider_llm")):
                    if key not in have:
                        jobs.append((repo_name, task, variant, llm))
    # Finished observations are kept in a cache, so a failure never loses the work done.
    repos = "-".join(sorted(args.repo or ["all"]))
    cache_path = Path(tempfile.gettempdir()) / f"counterproof-observe-{repos}.json"
    cache: dict[str, list[str]] = (
        json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    )
    lock = threading.Lock()

    def run(job: tuple[str, str, str, bool]) -> None:
        key = "/".join(map(str, job))
        if key in cache:
            return
        try:
            found = observe(*job)
        except Exception as exc:
            print(f"FAILED {key}: {exc}", flush=True)
            return
        with lock:
            cache[key] = list(found)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
        print(f"done {key}: {found[0]} {found[1]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(run, jobs))
    missing = [j for j in jobs if "/".join(map(str, j)) not in cache]
    if missing:
        print(f"{len(missing)} observation(s) failed; re-run to retry them", file=sys.stderr)
        return 1
    results = [(j, tuple(cache["/".join(map(str, j))])) for j in jobs]

    text = EXPECTATIONS.read_text(encoding="utf-8")
    lines = text.split("\n")
    repo_at = {name: lines.index(f"  {name}:") for name in ("py_ledger", "ts_cart")}
    for (repo_name, task, variant, llm), (verdict, rule, why) in results:
        start = repo_at[repo_name]
        t = next(i for i in range(start, len(lines)) if lines[i] == f"      {task}:")
        v = next(i for i in range(t, len(lines)) if lines[i].startswith(f"          {variant}: {{"))
        key = "provider_llm" if llm else "provider_none"
        addition = f", {key}: {_entry(verdict, rule)}, {key}_why: {json.dumps(why)}}}"
        assert lines[v].endswith("}"), lines[v]
        lines[v] = lines[v][:-1] + addition
        print(f"{repo_name:<10} {task:<26} {variant:<9} {key:<13} {verdict:<12} {rule}  {why}")
    EXPECTATIONS.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
