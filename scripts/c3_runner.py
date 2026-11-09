"""Corpus C3 (PRD section 17.1): real merged pull requests written with a coding agent.

Runs in GitHub Actions (.github/workflows/eval-c3.yml), never on a developer machine: each
pull request is cloned, its dependencies are installed and its tests run, which executes
foreign code. The stages are

  plan    resolve each candidate in eval/corpora/c3/candidates.json to its base and head
          commits through the GitHub API, and write the job matrix (the only stage that
          uses a token)
  case    one pull request: clone it, install its test dependencies into a virtualenv
          (best effort, logged), then ``counterproof eval`` on a one-case corpus with the
          rule-only configuration. No token and no secret is present in this job
  merge   combine the per-case reports into eval/results/<date>-c3.{json,md}, with a
          per-case setup log so INCONCLUSIVE results can be told apart from verdicts

    python scripts/c3_runner.py plan --out matrix.json [--limit N]
    python scripts/c3_runner.py case --spec '<json>' --workdir W --out report.json
    python scripts/c3_runner.py merge --reports DIR --out eval/results/<date>-c3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "eval" / "corpora" / "c3" / "candidates.json"
DESCRIPTION = (
    "Corpus C3: merged pull requests from public Python repositories whose description says "
    "they were generated with Claude Code (one per repository, GitHub search, October to "
    "November 2026). They are assumed honest, so every UNPROVEN or REFUTED verdict counts as "
    "a false alarm (PRD 17.1) and is reviewed by hand. Each pull request ran on its own "
    "GitHub Actions runner with its dependencies installed best effort; INCONCLUSIVE means "
    "Counterproof could not run the project's tests (setup log per case)."
)
EXTRAS = ("test", "tests", "testing", "dev")
REQUIREMENTS = (
    "requirements-dev.txt",
    "requirements_dev.txt",
    "dev-requirements.txt",
    "requirements-test.txt",
    "requirements_test.txt",
    "test-requirements.txt",
    "requirements/dev.txt",
    "requirements/test.txt",
    "requirements.txt",
)


def _run(
    cmd: list[str], cwd: Path, log: list[str], stderr_file: Path | None = None, **env: str
) -> bool:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=1200,
            check=False,
        )
    except subprocess.TimeoutExpired:  # a slow suite is a result to record, not a crash
        log.append(f"$ {' '.join(cmd)}  -> timed out after 1200 s")
        return False
    log.append(f"$ {' '.join(cmd)}  -> {proc.returncode}")
    if stderr_file is not None and proc.stderr:  # all of it: a crash dump lists every thread
        stderr_file.parent.mkdir(parents=True, exist_ok=True)
        stderr_file.write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        log.append(proc.stdout[-1000:] + proc.stderr[-3000:])  # a crash's stack is on stderr
    return proc.returncode == 0


def plan(out: Path, limit: int | None, only: list[str] | None = None) -> int:
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
    if only:
        candidates = [c for c in candidates if f"{c['repository']}#{c['number']}" in only]
    matrix: list[dict[str, Any]] = []
    for c in candidates[:limit]:
        proc = subprocess.run(
            ["gh", "api", f"repos/{c['repository']}/pulls/{c['number']}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"skip {c['repository']}#{c['number']}: {proc.stderr.strip()[:200]}")
            continue
        pr = json.loads(proc.stdout)
        matrix.append(
            {
                "id": f"{c['repository']}#{c['number']}",
                "repository": c["repository"],
                "number": c["number"],
                "base": pr["base"]["sha"],
                "head": pr["head"]["sha"],
                "title": pr.get("title", ""),
            }
        )
    out.write_text(json.dumps(matrix), encoding="utf-8")
    print(f"{len(matrix)} of {len(candidates[:limit])} candidates resolved")
    return 0


def _install(src: Path, log: list[str]) -> tuple[bool, bool]:
    """Best effort: the project and its test dependencies into ``src/.venv``.

    ``(venv has pytest, project dependencies installed)``. The venv is used whenever it has
    pytest: a project whose own install failed may still collect its tests."""
    if not _run(["uv", "venv", ".venv", "--python", "3.12"], src, log):
        return False, False
    python = str(src / ".venv" / "bin" / "python")
    pip = ["uv", "pip", "install", "--python", python]
    installed = False
    if (src / "uv.lock").is_file():
        installed = _run(
            ["uv", "sync", "--all-extras", "--frozen"], src, log, UV_PROJECT_ENVIRONMENT=".venv"
        )
    if not installed and ((src / "pyproject.toml").is_file() or (src / "setup.py").is_file()):
        for extra in EXTRAS:
            if _run([*pip, "-e", f".[{extra}]"], src, log):
                installed = True
                break
        installed = installed or _run([*pip, "-e", "."], src, log)
    for name in REQUIREMENTS:
        if (src / name).is_file():
            installed = _run([*pip, "-r", name], src, log) or installed
    return _run([*pip, "pytest", "pytest-cov"], src, log), installed


def case(spec: dict[str, Any], workdir: Path, out: Path) -> int:
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "src"
    log: list[str] = []
    url = f"https://github.com/{spec['repository']}"
    ok = _run(["git", "clone", "--quiet", url, str(src)], workdir, log)  # full: base blobs too
    ok = ok and _run(
        ["git", "fetch", "--quiet", "origin", f"pull/{spec['number']}/head:pr-head"], src, log
    )
    base = ""
    if ok:
        merge_base = subprocess.run(
            ["git", "merge-base", spec["base"], spec["head"]],
            cwd=src,
            capture_output=True,
            text=True,
            check=False,
        )
        base = merge_base.stdout.strip() or spec["base"]
        ok = _run(["git", "checkout", "--quiet", spec["head"]], src, log)
    venv_ready, installed = _install(src, log) if ok else (False, False)
    corpus = workdir / "corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "corpus.toml").write_text(
        "\n".join(
            [
                f'name = "C3 {spec["id"]}"',
                'seed = "00000000000000000000000000000000"',
                "[[configurations]]",
                'name = "rules"',
                'provider = "none"',
                "[[cases]]",
                f"id = {json.dumps(spec['id'])}",
                f"git = {json.dumps(src.as_posix())}",
                f'base = "{base or spec["base"]}"',
                f'head = "{spec["head"]}"',
                'label = { label = "honest", kind = "agent pull request" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {"COUNTERPROOF_PYTHON": str(src / ".venv" / "bin" / "python")} if venv_ready else {}
    env["PYTHONFAULTHANDLER"] = "1"  # a native crash prints the Python stack to the log
    ran = _run(
        [
            "counterproof",
            "eval",
            str(corpus),
            "--allow-remote",
            "--jobs",
            "1",
            "--out",
            str(out),
        ],
        workdir,
        log,
        out.with_suffix(".stderr.txt"),
        **env,
    )
    # Each result carries the verdict's reasons (rule, file, line), which is what the hand
    # review of a flagged case starts from; the run directories stay in eval's temp cache.
    setup = {
        "id": spec["id"],
        "cloned": ok,
        "dependencies_installed": installed,
        "test_venv": venv_ready,
        "eval_ran": ran,
        "log": log[-40:],
    }
    out.parent.mkdir(parents=True, exist_ok=True)  # eval may have failed before writing it
    out.with_suffix(".setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    shutil.rmtree(src, ignore_errors=True)
    return 0


def merge(reports_dir: Path, out: Path) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from counterproof.eval.harness import load_report, merge_reports, write_report  # noqa: PLC0415

    reports = [
        load_report(p)
        for p in sorted(reports_dir.rglob("*.json"))
        if not p.name.endswith(".setup.json")
    ]
    setups = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(reports_dir.rglob("*.setup.json"))
    ]
    merged = merge_reports(reports, corpus="C3 real agent pull requests", description=DESCRIPTION)
    json_path, md_path = write_report(merged, out)
    setup_path = json_path.with_name(json_path.stem + "-setup.json")
    setup_path.write_text(json.dumps(setups, indent=2) + "\n", encoding="utf-8")
    print(f"{len(merged.results)} results from {len(setups)} pull requests -> {md_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--out", type=Path, required=True)
    p_plan.add_argument("--limit", type=int, default=None)
    p_plan.add_argument("--only", default="", help="comma-separated owner/repo#number ids")
    p_case = sub.add_parser("case")
    p_case.add_argument("--spec", required=True, help="one matrix entry as JSON")
    p_case.add_argument("--workdir", type=Path, required=True)
    p_case.add_argument("--out", type=Path, required=True)
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--reports", type=Path, required=True)
    p_merge.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.stage == "plan":
        return plan(args.out, args.limit, [i for i in args.only.split(",") if i] or None)
    if args.stage == "case":
        return case(json.loads(args.spec), args.workdir, args.out)
    return merge(args.reports, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
