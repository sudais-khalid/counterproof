"""JestAdapter (FR-AD-03), checked against Jest 30.5 and jest-junit 17 (November 2026).

* Run: ``jest --ci --reporters=jest-junit --runTestsByPath <files> -t '^(?:names)$'``.
  jest-junit is configured through its environment variables: ``classname`` is the file
  path, ``name`` is ``<describe> > ... > <title>`` (ancestors joined by `` > ``), so node
  ids match Vitest's: ``<file>::<full name>``. Jest's ``-t`` matches the full name joined
  with spaces. Suite load errors are reported as a case named after the file.
* List: ``jest --listTests --json`` gives files; test names come from a static scan of each
  file (tree-sitter). Related tests: ``jest --listTests --findRelatedTests`` (runs nothing).
* No per-test coverage in v1. Jest's cache goes to the run directory.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from counterproof.adapters.base import (
    NOT_REPORTED,
    CollectionError,
    CoverageMap,
    PrepResult,
    classify_failure,
)
from counterproof.adapters.junit import JUnitCase, parse_junit
from counterproof.adapters.node import (
    declared_dependencies,
    ensure_node_modules,
    js_regex_escape,
    node_env,
    node_version,
    package_bin,
    resolve_node,
)
from counterproof.change.classify import is_test_path
from counterproof.evidence.hashing import sha256_bytes, sha256_file
from counterproof.log import get_logger
from counterproof.models import FailureInfo, FailureKind, RunResult, TestId, TestOutcome
from counterproof.probes import tsast
from counterproof.workspace.manager import Workspace
from counterproof.workspace.sandbox_docker import SandboxHost

log = get_logger("adapters.jest")

COLLECT_TIMEOUT_S: Final = 180.0
MAX_CMDLINE_CHARS: Final = 7000
SUITE_ERROR: Final = "Test suite failed to run"
CONFIG_FILES: Final = tuple(
    f"jest.config.{ext}" for ext in ("js", "cjs", "mjs", "ts", "cts", "mts", "json")
)


class JestAdapter(SandboxHost):
    name = "jest"

    def __init__(
        self,
        repo_root: Path,
        *,
        env_pass: Sequence[str] = (),
        source_env: Mapping[str, str] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.env_pass = tuple(env_pass)
        self.source_env: Mapping[str, str] = os.environ if source_env is None else source_env
        self.node = resolve_node(self.source_env)
        self._listed: Workspace | None = None

    def detect(self, repo: Path) -> float:
        deps = declared_dependencies(repo)
        if any((repo / name).is_file() for name in CONFIG_FILES):
            return 1.0
        if "jest" in deps and "vitest" not in deps:
            return 0.9
        return 0.4 if "jest" in deps else 0.0

    def is_test_file(self, path: str) -> bool:
        return tsast.is_ts_path(path) and is_test_path(path)

    def child_env(self, ws: Workspace, junit_path: Path | None = None) -> dict[str, str]:
        env = node_env(self.source_env, self.env_pass, self.node)
        env.update(
            JEST_JUNIT_CLASSNAME="{filepath}",
            JEST_JUNIT_TITLE="{classname} > {title}",
            JEST_JUNIT_ANCESTOR_SEPARATOR=" > ",
            JEST_JUNIT_REPORT_TEST_SUITE_ERRORS="true",
        )
        if junit_path is not None:
            env["JEST_JUNIT_OUTPUT_FILE"] = str(junit_path)
        return env

    def _bin(self, ws: Workspace) -> Path | None:
        return package_bin(ws.root / "node_modules", "jest", "jest")

    def _node(self) -> str:
        if self.node is None:
            raise CollectionError("node not found on PATH (set COUNTERPROOF_NODE)")
        return self.node

    def _base_cmd(self, ws: Workspace) -> list[str]:
        return [
            self._node(),
            str(self._bin(ws)),
            "--ci",
            "--rootDir",
            str(ws.root),
            "--cacheDirectory",
            str(ws.root.parent / f".jest-cache-{ws.name}"),
            "--watchman=false",
        ]

    def prepare(self, ws: Workspace) -> PrepResult:
        if self.node is None:
            return PrepResult(
                ok=False, message="node not found on PATH (set COUNTERPROOF_NODE to its path)"
            )
        problem = ensure_node_modules(self.repo_root, ws)
        if problem:
            return PrepResult(ok=False, message=problem)
        missing = [p for p in ("jest", "jest-junit") if not (ws.root / "node_modules" / p).is_dir()]
        if self._bin(ws) is None or missing:
            return PrepResult(
                ok=False,
                message=f"{' and '.join(missing or ['jest'])} not installed in node_modules; "
                "install the project's dependencies first (FR-AD-05)",
            )
        env = self.child_env(ws)
        version = node_version(self.node, env, ws.root)
        if version is None:
            return PrepResult(ok=False, message=f"cannot run {self.node}")
        data = json.loads(
            (ws.root / "node_modules" / "jest" / "package.json").read_text(encoding="utf-8")
        )
        log.info("jest %s on node %s (%s)", data.get("version"), version, self.node)
        return PrepResult(
            ok=True,
            interpreter=self.node,
            env=env,
            capabilities=frozenset({"jest"}),
            message=f"jest {data.get('version')}, node {version}",
        )

    def _list_files(self, ws: Workspace, extra: list[str]) -> list[str]:
        cmd = [*self._base_cmd(ws), "--listTests", "--json", *extra]
        proc = self.run_process(
            cmd, cwd=ws.root, env=self.child_env(ws), timeout_s=COLLECT_TIMEOUT_S
        )
        start = proc.stdout.find("[")
        try:
            files = json.loads(proc.stdout[start:]) if start >= 0 else None
        except json.JSONDecodeError:
            files = None
        if proc.timed_out or proc.exit_code != 0 or not isinstance(files, list):
            raise CollectionError(
                f"jest --listTests failed (exit {proc.exit_code})", proc.stdout + proc.stderr
            )
        root = ws.root.resolve()
        out: list[str] = []
        for f in files:
            try:
                out.append(Path(str(f)).resolve().relative_to(root).as_posix())
            except ValueError:
                continue
        return sorted(out)

    def list_tests(self, ws: Workspace, files: list[str] | None = None) -> list[TestId]:
        problem = ensure_node_modules(self.repo_root, ws)
        if problem:
            raise CollectionError(problem)
        listed = self._list_files(ws, [])
        if files:
            listed = [f for f in listed if f in set(files)]
        self._listed = ws
        tests: list[TestId] = []
        for rel in listed:
            text = (ws.root / rel).read_text(encoding="utf-8", errors="replace")
            analysed = tsast.analyse_test_file(text, rel)
            if analysed is None:
                raise CollectionError(f"cannot parse test file {rel}")
            tests += [TestId(adapter=self.name, file=rel, name=n) for n in analysed.tests]
        return tests

    def related_tests(self, src_files: list[str], all_tests: list[TestId]) -> list[TestId]:
        if self._listed is None or not src_files:
            return []
        try:
            files = set(self._list_files(self._listed, ["--findRelatedTests", *src_files]))
        except CollectionError as exc:
            log.warning("jest --findRelatedTests failed: %s", exc)
            return []
        return [t for t in all_tests if t.file in files]

    def _targets(self, tests: list[str]) -> list[str]:
        files = sorted({t.partition("::")[0] for t in tests})
        names = sorted({t.partition("::")[2].replace(" > ", " ") for t in tests})
        pattern = "^(?:" + "|".join(js_regex_escape(n) for n in names) + ")$"
        args = ["--runTestsByPath", *files, "-t", pattern]
        if sum(len(a) + 1 for a in args) > MAX_CMDLINE_CHARS:
            return ["--runTestsByPath", *files]
        return args

    def run(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        fail_fast: bool,
        junit_path: Path,
        coverage_file: Path | None = None,
    ) -> RunResult:
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.unlink(missing_ok=True)
        problem = ensure_node_modules(self.repo_root, ws)
        cmd = [*self._base_cmd(ws), "--reporters=jest-junit"]
        if fail_fast:
            cmd.append("--bail=1")
        cmd += self._targets(tests)
        if problem:
            out, exit_code, duration, timed_out = problem, 1, 0.0, False
        else:
            proc = self.run_process(
                cmd, cwd=ws.root, env=self.child_env(ws, junit_path), timeout_s=timeout_s
            )
            out = f"{proc.stdout}\n{proc.stderr}"
            exit_code, duration, timed_out = proc.exit_code, proc.duration_s, proc.timed_out
        junit_path.with_suffix(".log").write_text(
            f"$ {' '.join(cmd)}\n[exit {exit_code}{' TIMEOUT' if timed_out else ''}]\n{out}",
            encoding="utf-8",
        )
        outcomes, failures, durations = self._map_results(
            parse_junit(junit_path), tests, timed_out=timed_out, fail_fast=fail_fast
        )
        return RunResult(
            tests=outcomes,
            failures=failures,
            duration_s=round(duration, 6),
            command=cmd,
            exit_code=exit_code,
            junit_sha256=sha256_file(junit_path) if junit_path.is_file() else sha256_bytes(b""),
            test_durations=dict(sorted(durations.items())),
        )

    def _map_results(
        self, cases: list[JUnitCase], tests: list[str], *, timed_out: bool, fail_fast: bool
    ) -> tuple[dict[str, TestOutcome], dict[str, FailureInfo], dict[str, float]]:
        wanted = set(tests)
        outcomes: dict[str, TestOutcome] = {}
        failures: dict[str, FailureInfo] = {}
        durations: dict[str, float] = {}
        for case in cases:
            if case.classname == SUITE_ERROR:
                file = case.name.replace("\\", "/")
                for t in tests:
                    if t.partition("::")[0] == file and t not in outcomes:
                        outcomes[t] = "error"
                        failures[t] = FailureInfo(
                            kind="COLLECTION", message=SUITE_ERROR, traceback_excerpt=case.text
                        )
                continue
            file = case.classname.replace("\\", "/")
            name = case.name.removeprefix(" > ")
            nodeid = f"{file}::{name}"
            if nodeid not in wanted:
                continue
            outcomes[nodeid] = case.outcome
            durations[nodeid] = case.time_s
            if case.outcome in {"fail", "error"}:
                failures[nodeid] = FailureInfo(
                    kind=self.failure_kind(case), message=case.message, traceback_excerpt=case.text
                )
        stopped_early = fail_fast and any(o in {"fail", "error"} for o in outcomes.values())
        for t in tests:
            if t in outcomes:
                continue
            if timed_out:
                outcomes[t] = "timeout"
                failures[t] = FailureInfo(kind="TIMEOUT", message="run timed out")
            elif not stopped_early:
                outcomes[t] = "error"
                failures[t] = FailureInfo(kind="COLLECTION", message=NOT_REPORTED)
        return outcomes, failures, durations

    def failure_kind(self, case: JUnitCase) -> FailureKind:
        return classify_failure(case)

    def run_script(
        self, ws: Workspace, script: Path, args: list[str], *, timeout_s: float = 120.0
    ) -> str | None:
        """Run a generated Jest test file inside the worktree (fast-check fuzzer)."""
        if ensure_node_modules(self.repo_root, ws) or self.node is None:
            return None
        rel = script.relative_to(ws.root).as_posix()
        proc = self.run_process(
            [*self._base_cmd(ws), "--runTestsByPath", rel],
            cwd=ws.root,
            env=self.child_env(ws),
            timeout_s=timeout_s,
        )
        return None if proc.timed_out or proc.exit_code != 0 else proc.stdout + proc.stderr

    def coverage_map(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        junit_path: Path,
        coverage_file: Path,
    ) -> CoverageMap | None:
        return None
