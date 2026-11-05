"""PytestAdapter (FR-AD-01, FR-AD-04, FR-AD-05).

Runs pytest in the user's existing Python environment inside a worktree: nothing is
installed. Results come from junit xml (``junit_family=xunit2``); the line-to-test map comes
from coverage.py dynamic contexts recorded by pytest-cov (``--cov-context=test``) and read
back with ``CoverageData.contexts_by_lineno`` inside the user's interpreter, so the reader
always matches the coverage version that wrote the data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Final

from counterproof.adapters.base import CollectionError, CoverageMap, PrepResult, classify_failure
from counterproof.adapters.junit import JUnitCase, junit_key, module_of, parse_junit
from counterproof.change.classify import is_test_path
from counterproof.evidence.hashing import sha256_bytes, sha256_file
from counterproof.git import parse_version
from counterproof.log import get_logger
from counterproof.models import FailureInfo, FailureKind, RunResult, TestId, TestOutcome
from counterproof.workspace.env import scrub_env
from counterproof.workspace.manager import Workspace
from counterproof.workspace.sandbox_none import run_process

log = get_logger("adapters.pytest")

INTERPRETER_ENV: Final = "COUNTERPROOF_PYTHON"
COLLECT_TIMEOUT_S: Final = 300.0
PROBE_TIMEOUT_S: Final = 60.0
MAX_CMDLINE_CHARS: Final = 24_000
_NODEID_RE: Final = re.compile(r"^(?P<file>[^\s:][^:]*\.py)::(?P<name>\S.*)$")
_CONFIG_FILES: Final = ("pytest.ini", "conftest.py")

_PROBE: Final = (
    "import importlib.util as u, json, sys\n"
    "def v(m):\n"
    "    if u.find_spec(m) is None:\n"
    "        return None\n"
    "    try:\n"
    "        return getattr(__import__(m), '__version__', 'unknown')\n"
    "    except Exception:\n"
    "        return None\n"
    "print(json.dumps({'python': sys.version.split()[0], 'pytest': v('pytest'),\n"
    "    'pytest_cov': u.find_spec('pytest_cov') is not None, 'coverage': v('coverage')}))\n"
)

# Reads coverage contexts with the user's own coverage.py (FR-AD-01).
_COVERAGE_READER: Final = (
    "import json, sys\n"
    "from coverage import CoverageData\n"
    "d = CoverageData(basename=sys.argv[1])\n"
    "d.read()\n"
    "out = {}\n"
    "for f in d.measured_files():\n"
    "    out[f] = {str(k): sorted(v) for k, v in d.contexts_by_lineno(f).items()}\n"
    "json.dump(out, sys.stdout)\n"
)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def resolve_interpreter(repo_root: Path, env: Mapping[str, str]) -> str:
    """``COUNTERPROOF_PYTHON``, then the repo's ``.venv``/``venv``, ``$VIRTUAL_ENV``, then us."""
    if explicit := env.get(INTERPRETER_ENV):
        return explicit
    for name in (".venv", "venv"):
        candidate = _venv_python(repo_root / name)
        if candidate.is_file():
            return str(candidate)
    if virtual_env := env.get("VIRTUAL_ENV"):
        candidate = _venv_python(Path(virtual_env))
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _venv_root(interpreter: str) -> Path | None:
    parent = Path(interpreter).resolve().parent.parent
    return parent if (parent / "pyvenv.cfg").is_file() else None


class PytestAdapter:
    name = "pytest"

    def __init__(
        self,
        repo_root: Path,
        *,
        env_pass: Sequence[str] = (),
        source_env: Mapping[str, str] | None = None,
        interpreter: str | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.env_pass = tuple(env_pass)
        self.source_env: Mapping[str, str] = os.environ if source_env is None else source_env
        self.interpreter = interpreter or resolve_interpreter(repo_root, self.source_env)
        self.capabilities: frozenset[str] = frozenset()
        self.pytest_version: tuple[int, int, int] | None = None

    # -- discovery ---------------------------------------------------------------------

    def detect(self, repo: Path) -> float:
        """Confidence that ``repo`` uses pytest."""
        if any((repo / name).is_file() for name in _CONFIG_FILES):
            return 1.0
        for name, marker in (
            ("pyproject.toml", "[tool.pytest"),
            ("setup.cfg", "[tool:pytest]"),
            ("tox.ini", "[pytest]"),
        ):
            path = repo / name
            if path.is_file() and marker in path.read_text(encoding="utf-8", errors="replace"):
                return 1.0
        saw_python = False
        for root, dirs, files in os.walk(repo):
            dirs[:] = [
                d for d in dirs if not d.startswith(".") and d not in {"node_modules", "venv"}
            ]
            for f in files:
                if f.endswith(".py"):
                    saw_python = True
                    rel = Path(root, f).relative_to(repo).as_posix()
                    if self.is_test_file(rel):
                        return 0.8
        return 0.3 if saw_python else 0.0

    def is_test_file(self, path: str) -> bool:
        return path.endswith(".py") and is_test_path(path)

    # -- environment -------------------------------------------------------------------

    def child_env(self, ws: Workspace, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """FR-WS-04 scrubbed environment plus what pytest needs to import the worktree."""
        env = scrub_env(self.source_env, self.env_pass)
        paths = [str(ws.root / "src")] if (ws.root / "src").is_dir() else []
        paths.append(str(ws.root))
        # The worktree must shadow any editable install of the user's own tree.
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        venv = _venv_root(self.interpreter)
        if venv is not None:
            env["VIRTUAL_ENV"] = str(venv)
            bin_dir = _venv_python(venv).parent
            env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
        if extra:
            env.update(extra)
        return env

    def prepare(self, ws: Workspace) -> PrepResult:
        """Check the interpreter has pytest. FR-AD-05: never install anything."""
        if not (Path(self.interpreter).is_file() or shutil.which(self.interpreter)):
            return PrepResult(ok=False, message=f"Python interpreter not found: {self.interpreter}")
        try:
            proc = run_process(
                [self.interpreter, "-E", "-c", _PROBE],
                cwd=ws.root.parent,  # not the worktree: repo files must not shadow stdlib here
                env=self.child_env(ws),
                timeout_s=PROBE_TIMEOUT_S,
            )
        except OSError as exc:
            return PrepResult(ok=False, message=f"cannot start {self.interpreter}: {exc}")
        try:
            info = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return PrepResult(
                ok=False,
                message=f"cannot probe {self.interpreter}: {proc.stderr.strip()[-500:]}",
            )
        if not info.get("pytest"):
            return PrepResult(
                ok=False,
                interpreter=self.interpreter,
                message=(
                    f"pytest is not installed for {self.interpreter}. Install it in the "
                    f"project environment or set {INTERPRETER_ENV}; Counterproof does not "
                    "install packages unless --allow-install is given (FR-AD-05)."
                ),
            )
        caps = {"pytest"}
        if info.get("pytest_cov") and info.get("coverage"):
            caps.add("coverage_contexts")
        self.capabilities = frozenset(caps)
        self.pytest_version = parse_version(str(info["pytest"]))
        log.info("pytest %s on Python %s (%s)", info["pytest"], info["python"], self.interpreter)
        return PrepResult(
            ok=True,
            interpreter=self.interpreter,
            env=self.child_env(ws),
            capabilities=self.capabilities,
            message=f"pytest {info['pytest']}, Python {info['python']}",
        )

    def _base_cmd(self, ws: Workspace) -> list[str]:
        return [self.interpreter, "-m", "pytest", "-p", "no:cacheprovider", f"--rootdir={ws.root}"]

    # -- collection --------------------------------------------------------------------

    def list_tests(self, ws: Workspace, files: list[str] | None = None) -> list[TestId]:
        cmd = [*self._base_cmd(ws), "--collect-only", "-q", *(files or [])]
        proc = run_process(cmd, cwd=ws.root, env=self.child_env(ws), timeout_s=COLLECT_TIMEOUT_S)
        if proc.exit_code == 5:  # no tests collected
            return []
        if proc.exit_code != 0 or proc.timed_out:
            raise CollectionError(
                f"pytest collection failed (exit {proc.exit_code})", proc.stdout + proc.stderr
            )
        tests: list[TestId] = []
        for line in proc.stdout.splitlines():
            match = _NODEID_RE.match(line.strip())
            if match:
                tests.append(TestId(adapter=self.name, file=match["file"], name=match["name"]))
        return tests

    def related_tests(self, src_files: list[str], all_tests: list[TestId]) -> list[TestId]:
        """FR-IS-02 name heuristic: ``foo.py`` maps to ``test_foo.py`` and ``foo_test.py``."""
        wanted: set[str] = set()
        for src in src_files:
            stem = PurePosixPath(src).stem
            wanted |= {f"test_{stem}.py", f"{stem}_test.py"}
        return [t for t in all_tests if PurePosixPath(t.file).name in wanted]

    # -- execution ---------------------------------------------------------------------

    def _targets(self, tests: list[str], args_file: Path) -> list[str]:
        """Node ids, or an ``@file`` (pytest >= 8.2) when the command line would be too long."""
        if sum(len(t) + 1 for t in tests) <= MAX_CMDLINE_CHARS:
            return list(tests)
        if self.pytest_version and self.pytest_version >= (8, 2, 0):
            args_file.write_text("\n".join(tests) + "\n", encoding="utf-8")
            return [f"@{args_file}"]
        return sorted({t.partition("::")[0] for t in tests})

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
        cmd = [*self._base_cmd(ws), "-q", f"--junitxml={junit_path}", "-o", "junit_family=xunit2"]
        if fail_fast:
            cmd.append("-x")
        extra_env: dict[str, str] = {}
        if coverage_file is not None:
            cmd += [f"--cov={ws.root}", "--cov-context=test", "--cov-report=", "--cov-fail-under=0"]
            extra_env["COVERAGE_FILE"] = str(coverage_file)
        elif "coverage_contexts" in self.capabilities:
            cmd.append("--no-cov")  # neutralise --cov in the project's addopts
        cmd += self._targets(tests, junit_path.with_suffix(".args"))
        proc = run_process(cmd, cwd=ws.root, env=self.child_env(ws, extra_env), timeout_s=timeout_s)
        junit_path.with_suffix(".log").write_text(
            f"$ {' '.join(cmd)}\n[exit {proc.exit_code}{' TIMEOUT' if proc.timed_out else ''}]\n"
            f"{proc.stdout}\n{proc.stderr}",
            encoding="utf-8",
        )
        cases = parse_junit(junit_path)
        outcomes, failures = self._map_results(
            cases, tests, timed_out=proc.timed_out, fail_fast=fail_fast
        )
        by_key = {junit_key(t): t for t in tests}
        durations = {
            by_key[(c.classname, c.name)]: c.time_s
            for c in cases
            if (c.classname, c.name) in by_key
        }
        return RunResult(
            tests=outcomes,
            failures=failures,
            duration_s=round(proc.duration_s, 6),
            command=cmd,
            exit_code=proc.exit_code,
            junit_sha256=sha256_file(junit_path) if junit_path.is_file() else sha256_bytes(b""),
            test_durations=dict(sorted(durations.items())),
        )

    def _map_results(
        self,
        cases: list[JUnitCase],
        tests: list[str],
        *,
        timed_out: bool,
        fail_fast: bool,
    ) -> tuple[dict[str, TestOutcome], dict[str, FailureInfo]]:
        by_key = {junit_key(t): t for t in tests}
        outcomes: dict[str, TestOutcome] = {}
        failures: dict[str, FailureInfo] = {}
        for case in cases:
            if case.is_collection_error:
                for t in tests:
                    if module_of(t.partition("::")[0]) == case.name and t not in outcomes:
                        outcomes[t] = "error"
                        failures[t] = FailureInfo(
                            kind="COLLECTION", message=case.message, traceback_excerpt=case.text
                        )
                continue
            nodeid = by_key.get((case.classname, case.name))
            if nodeid is None:
                continue  # not requested (file-level fallback ran extra tests)
            outcomes[nodeid] = case.outcome
            if case.outcome in {"fail", "error"}:
                failures[nodeid] = FailureInfo(
                    kind=self.failure_kind(case),
                    message=case.message,
                    traceback_excerpt=case.text,
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
                failures[t] = FailureInfo(kind="COLLECTION", message="no result reported")
        return outcomes, failures

    def failure_kind(self, case: JUnitCase) -> FailureKind:
        return classify_failure(case)

    # -- coverage ----------------------------------------------------------------------

    def coverage_map(
        self,
        ws: Workspace,
        tests: list[str],
        *,
        timeout_s: float,
        junit_path: Path,
        coverage_file: Path,
    ) -> CoverageMap | None:
        """FR-AD-01: changed line -> covering test ids, or ``None`` if unavailable."""
        if "coverage_contexts" not in self.capabilities:
            log.info("pytest-cov/coverage not installed: no coverage map (FR-IS-02 fallback)")
            return None
        coverage_file.unlink(missing_ok=True)
        self.run(
            ws,
            tests,
            timeout_s=timeout_s,
            fail_fast=False,
            junit_path=junit_path,
            coverage_file=coverage_file,
        )
        if not coverage_file.is_file():
            log.warning("coverage run produced no data file")
            return None
        proc = run_process(
            [self.interpreter, "-E", "-c", _COVERAGE_READER, str(coverage_file)],
            cwd=coverage_file.parent,
            env=self.child_env(ws),
            timeout_s=PROBE_TIMEOUT_S,
        )
        try:
            raw: dict[str, dict[str, list[str]]] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            log.warning("cannot read coverage contexts: %s", proc.stderr.strip()[-500:])
            return None
        return _to_coverage_map(raw, ws.root)


def _to_coverage_map(raw: Mapping[str, Mapping[str, list[str]]], ws_root: Path) -> CoverageMap:
    root = str(ws_root.resolve())
    prefix = os.path.normcase(root) + os.sep
    result: CoverageMap = {}
    for abs_file, lines in raw.items():
        resolved = str(Path(abs_file).resolve())
        if not os.path.normcase(resolved).startswith(prefix):
            continue
        # normcase never changes length, so slicing keeps the file's real spelling.
        rel = Path(resolved[len(prefix) :]).as_posix()
        for line, contexts in lines.items():
            tests = {c.rpartition("|")[0] or c for c in contexts if c}
            if tests:
                result[(rel, int(line))] = tests
    return result
