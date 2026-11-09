"""VitestAdapter (FR-AD-02), checked against Vitest 5.1 (November 2026).

* Run: ``vitest run <files> -t '^(?:full names)$' --reporter=junit --outputFile=<file>``.
  Vitest's junit ``classname`` is the file relative to the root and ``name`` is the full
  test name joined with `` > `` (``describe > it``); ``-t`` matches that same full name.
  Node ids are ``<file>::<full name>``.
* A file that fails to load (syntax error, failed import) is one case named after the file.
* List: ``vitest list --json``. Related tests: a static import graph (no tests run).
* Per-test coverage is not required in v1 (FR-AD-02), so ``coverage_map`` returns None.
* Caches go to the run directory, never into the (linked) ``node_modules``.
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
    name_pattern,
    node_env,
    node_version,
    package_bin,
    related_by_imports,
    resolve_node,
)
from counterproof.change.classify import is_test_path
from counterproof.evidence.hashing import sha256_bytes, sha256_file
from counterproof.log import get_logger
from counterproof.models import FailureInfo, FailureKind, RunResult, TestId, TestOutcome
from counterproof.probes import tsast
from counterproof.workspace.manager import Workspace
from counterproof.workspace.sandbox_docker import SandboxHost

log = get_logger("adapters.vitest")

COLLECT_TIMEOUT_S: Final = 180.0
MAX_CMDLINE_CHARS: Final = 7000
CONFIG_FILES: Final = tuple(
    f"vitest.config.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
) + tuple(f"vitest.workspace.{ext}" for ext in ("ts", "js", "json"))
_LOAD_ERRORS: Final = (
    "Transform failed",
    "PARSE_ERROR",
    "SyntaxError",
    "Failed to load url",
    "Failed to resolve import",
    "Cannot find module",
    "does not provide an export named",
    "ERR_MODULE_NOT_FOUND",
)


class VitestAdapter(SandboxHost):
    name = "vitest"

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

    # -- discovery -----------------------------------------------------------------------

    def detect(self, repo: Path) -> float:
        if any((repo / name).is_file() for name in CONFIG_FILES):
            return 1.0
        if "vitest" in declared_dependencies(repo):
            return 0.9
        return 0.0

    def is_test_file(self, path: str) -> bool:
        return tsast.is_ts_path(path) and is_test_path(path)

    # -- environment ---------------------------------------------------------------------

    def child_env(self, ws: Workspace) -> dict[str, str]:
        return node_env(self.source_env, self.env_pass, self.node)

    def _bin(self, ws: Workspace) -> Path | None:
        return package_bin(ws.root / "node_modules", "vitest", "vitest")

    def _cache_dir(self, ws: Workspace) -> Path:
        return ws.root.parent / f".vitest-cache-{ws.name}"

    def _ensure(self, ws: Workspace) -> str | None:
        return ensure_node_modules(self.repo_root, ws)

    def prepare(self, ws: Workspace) -> PrepResult:
        """FR-AD-05: check node and the project's vitest; never install anything."""
        if self.node is None:
            return PrepResult(
                ok=False, message="node not found on PATH (set COUNTERPROOF_NODE to its path)"
            )
        problem = self._ensure(ws)
        if problem:
            return PrepResult(ok=False, message=problem)
        if self._bin(ws) is None:
            return PrepResult(
                ok=False,
                message="vitest is not installed in node_modules; install the project's "
                "dependencies first (Counterproof does not install packages, FR-AD-05)",
            )
        env = self.child_env(ws)
        version = node_version(self.node, env, ws.root)
        if version is None:
            return PrepResult(ok=False, message=f"cannot run {self.node}")
        data = json.loads(
            (ws.root / "node_modules" / "vitest" / "package.json").read_text(encoding="utf-8")
        )
        log.info("vitest %s on node %s (%s)", data.get("version"), version, self.node)
        return PrepResult(
            ok=True,
            interpreter=self.node,
            env=env,
            capabilities=frozenset({"vitest"}),
            message=f"vitest {data.get('version')}, node {version}",
        )

    def _node(self) -> str:
        if self.node is None:
            raise CollectionError("node not found on PATH (set COUNTERPROOF_NODE)")
        return self.node

    def _base_cmd(self, ws: Workspace) -> list[str]:
        return [
            self._node(),
            str(self._bin(ws)),
            "run",
            "--root",
            str(ws.root),
            "--cache=false",
            f"--fsModuleCachePath={self._cache_dir(ws)}",
            "--passWithNoTests",
            "--watch=false",
        ]

    # -- collection ----------------------------------------------------------------------

    def list_tests(self, ws: Workspace, files: list[str] | None = None) -> list[TestId]:
        problem = self._ensure(ws)
        if problem:
            raise CollectionError(problem)
        cmd = [
            self._node(),
            str(self._bin(ws)),
            "list",
            "--json",
            "--root",
            str(ws.root),
            f"--fsModuleCachePath={self._cache_dir(ws)}",
            *(files or []),
        ]
        proc = self.run_process(
            cmd, cwd=ws.root, env=self.child_env(ws), timeout_s=COLLECT_TIMEOUT_S
        )
        start = proc.stdout.find("[")
        try:
            entries = json.loads(proc.stdout[start:]) if start >= 0 else None
        except json.JSONDecodeError:
            entries = None
        if proc.timed_out or not isinstance(entries, list):
            raise CollectionError(
                f"vitest list failed (exit {proc.exit_code})", proc.stdout + proc.stderr
            )
        self._listed = ws
        tests: list[TestId] = []
        root = ws.root.resolve()
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry or "file" not in entry:
                continue
            try:
                rel = Path(entry["file"]).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            tests.append(TestId(adapter=self.name, file=rel, name=str(entry["name"])))
        if proc.exit_code != 0 and not tests:
            raise CollectionError(
                f"vitest list failed (exit {proc.exit_code})", proc.stdout + proc.stderr
            )
        return tests

    def related_tests(self, src_files: list[str], all_tests: list[TestId]) -> list[TestId]:
        """FR-IS-02 via the import graph of the listed worktree (``vitest related`` runs
        the tests it finds; this finds the same files without running anything)."""
        if self._listed is None:
            return []
        root = self._listed.root

        def read(path: str) -> str | None:
            target = root / path
            try:
                return target.read_text(encoding="utf-8") if target.is_file() else None
            except OSError:
                return None

        files = sorted({t.file for t in all_tests})
        related = related_by_imports(files, src_files, read)
        return [t for t in all_tests if t.file in related]

    # -- execution -----------------------------------------------------------------------

    def _targets(self, tests: list[str]) -> list[str]:
        files = sorted({t.partition("::")[0] for t in tests})
        names = [t.partition("::")[2] for t in tests]
        args = [*files, "-t", name_pattern(names)]
        if sum(len(a) + 1 for a in args) > MAX_CMDLINE_CHARS:
            return files  # whole files; results are mapped back to the requested ids
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
        problem = self._ensure(ws)
        cmd = [
            *self._base_cmd(ws),
            "--reporter=junit",
            f"--outputFile={junit_path}",
        ]
        if fail_fast:
            cmd.append("--bail=1")
        cmd += self._targets(tests)
        if problem:
            proc_out, exit_code, duration, timed_out = problem, 1, 0.0, False
        else:
            proc = self.run_process(cmd, cwd=ws.root, env=self.child_env(ws), timeout_s=timeout_s)
            proc_out = f"{proc.stdout}\n{proc.stderr}"
            exit_code, duration, timed_out = proc.exit_code, proc.duration_s, proc.timed_out
        junit_path.with_suffix(".log").write_text(
            f"$ {' '.join(cmd)}\n[exit {exit_code}{' TIMEOUT' if timed_out else ''}]\n{proc_out}",
            encoding="utf-8",
        )
        cases = parse_junit(junit_path)
        outcomes, failures, durations = self._map_results(
            cases, tests, timed_out=timed_out, fail_fast=fail_fast
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
            if case.name == case.classname:
                # The file failed to load: every requested test in it is a collection error.
                for t in tests:
                    if t.partition("::")[0] == case.classname and t not in outcomes:
                        outcomes[t] = "error"
                        failures[t] = FailureInfo(
                            kind="COLLECTION",
                            message=case.message[:500],
                            traceback_excerpt=case.text,
                        )
                continue
            nodeid = f"{case.classname}::{case.name}"
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
        blob = f"{case.message}\n{case.text}"
        if any(e in blob for e in ("Transform failed", "PARSE_ERROR")):
            return "COLLECTION"
        return classify_failure(case)

    def run_script(
        self, ws: Workspace, script: Path, args: list[str], *, timeout_s: float = 120.0
    ) -> str | None:
        """Run a generated Vitest file (the fast-check fuzzer) inside the worktree.

        ``script`` must already be inside ``ws`` and carry its own inputs (``args`` is
        unused: no Counterproof-specific variable ever reaches a child process).
        """
        problem = self._ensure(ws)
        if problem or self.node is None:
            return None
        cmd = [*self._base_cmd(ws), "--reporter=dot", str(script.relative_to(ws.root).as_posix())]
        proc = self.run_process(cmd, cwd=ws.root, env=self.child_env(ws), timeout_s=timeout_s)
        return None if proc.timed_out or proc.exit_code != 0 else proc.stdout

    def coverage_map(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        junit_path: Path,
        coverage_file: Path,
    ) -> CoverageMap | None:
        """FR-AD-02: per-test coverage is not required in v1; selection uses related tests."""
        return None
