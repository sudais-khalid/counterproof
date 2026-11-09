"""Docker sandbox driver (FR-WS-05): run the project's tests in a locked-down container.

``--sandbox docker`` runs every test-runner process (baseline, red-green, mutants, fuzzing,
the blind oracle) inside a container with no network, 2 GB of memory, 2 CPUs, 512 processes
and the host user's uid, removed after each call. The run directory (worktrees, JUnit
reports, coverage files) is mounted at the same path inside the container, so every path the
adapters build stays valid.

* Python: an image ``counterproof-sandbox:py<version>-<hash>`` is built once per set of
  dependency files from the base commit (``python:<version>-slim``, pytest, pytest-cov and
  the project's test extras and requirements files); building is the only step with network.
* Node: the official ``node:<major>-slim`` image, with the project's own ``node_modules``
  mounted read-only. Native modules are built for the host, so Node projects need a Linux host.

Docker Desktop on Windows cannot mount host paths at the same path, so the sandbox refuses to
start there instead of silently running without isolation.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from counterproof.log import get_logger
from counterproof.workspace.sandbox_none import OUTPUT_LIMIT, ProcessResult, run_process

log = get_logger("sandbox")

LIMITS: Final = ("--network", "none", "--memory", "2g", "--cpus", "2", "--pids-limit", "512")
BUILD_TIMEOUT_S: Final = 1800.0
PROBE_TIMEOUT_S: Final = 60.0
DEPENDENCY_FILES: Final = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_dev.txt",
    "dev-requirements.txt",
    "requirements-test.txt",
    "requirements_test.txt",
    "test-requirements.txt",
)
_HOST_ONLY_ENV: Final = frozenset(
    {
        "PATH",
        "HOME",
        "TMP",
        "TEMP",
        "TMPDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "VIRTUAL_ENV",
        "PYTHONHOME",
    }
)
_DOCKERFILE_PY: Final = """\
FROM python:{version}-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY project /tmp/project
RUN set -e; cd /tmp/project; pip install pytest pytest-cov hypothesis; \\
    if [ -f pyproject.toml ] || [ -f setup.py ]; then \\
      pip install ".[test]" || pip install ".[tests]" || pip install ".[dev]" || pip install . \\
      || echo "counterproof: the project itself could not be installed"; fi; \\
    for f in requirements-dev.txt requirements_dev.txt dev-requirements.txt \\
             requirements-test.txt requirements_test.txt test-requirements.txt \\
             requirements.txt; do \\
      if [ -f "$f" ]; then pip install -r "$f"; fi; done; \\
    rm -rf /tmp/project
"""

Runner = Callable[..., ProcessResult]


class SandboxError(Exception):
    """The Docker sandbox cannot be used for this run (the message says why)."""


@dataclass
class DockerSandbox:
    image: str
    mounts: list[Path]
    """Host directories mounted read-write at the same path."""
    read_only: list[Path] = field(default_factory=list)
    """Host directories mounted read-only at the same path (a Node project's node_modules)."""
    tools: dict[str, str] = field(default_factory=dict)
    """Host executable -> executable inside the image (the interpreter, node)."""
    docker: str = "docker"
    runner: Runner = run_process
    uid: str | None = None

    def command(
        self, command: Sequence[str], *, cwd: Path, env: Mapping[str, str], name: str
    ) -> list[str]:
        cmd = [str(c) for c in command]
        if cmd and cmd[0] in self.tools:
            cmd[0] = self.tools[cmd[0]]
        out = [self.docker, "run", "--rm", "--init", "--name", name, *LIMITS]
        user = self.uid or _host_user()
        if user:
            out += ["--user", user]
        # The container's own /tmp (a tmpfs inside the sandbox, not the host's).
        out += ["--tmpfs", "/tmp:rw,exec", "-e", "HOME=/tmp"]  # noqa: S108
        for key, value in sorted(env.items()):
            if key.upper() not in _HOST_ONLY_ENV and "\n" not in value:
                out += ["-e", f"{key}={value}"]
        for path in self.mounts:
            out += ["-v", f"{path}:{path}"]
        for path in self.read_only:
            out += ["-v", f"{path}:{path}:ro"]
        out += ["-w", str(cwd), self.image, *cmd]
        return out

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        output_limit: int = OUTPUT_LIMIT,
    ) -> ProcessResult:
        name = f"counterproof-{uuid.uuid4().hex[:12]}"
        docker_cmd = self.command(command, cwd=cwd, env=env, name=name)
        host_env = {k: v for k, v in os.environ.items() if k.upper() in _HOST_ONLY_ENV}
        result = self.runner(
            docker_cmd, cwd=cwd, env=host_env, timeout_s=timeout_s, output_limit=output_limit
        )
        if result.timed_out:  # the client was killed; make sure the container is gone too
            self.runner(
                [self.docker, "kill", name], cwd=cwd, env=host_env, timeout_s=PROBE_TIMEOUT_S
            )
        return ProcessResult(
            command=[str(c) for c in command],
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_s=result.duration_s,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )


class SandboxHost:
    """Mixin for adapters: test-runner processes go through the sandbox when one is set."""

    sandbox: DockerSandbox | None = None

    def run_process(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        output_limit: int = OUTPUT_LIMIT,
    ) -> ProcessResult:
        if self.sandbox is None:
            return run_process(
                command, cwd=cwd, env=env, timeout_s=timeout_s, output_limit=output_limit
            )
        return self.sandbox.run(
            command, cwd=cwd, env=env, timeout_s=timeout_s, output_limit=output_limit
        )


def _host_user() -> str | None:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    return f"{getuid()}:{getgid()}" if getuid and getgid else None


def python_image_tag(base_root: Path, version: str) -> str:
    """``counterproof-sandbox:py3.12-<hash>``: one image per Python version and dependencies."""
    digest = hashlib.sha256(_DOCKERFILE_PY.encode())
    for name in DEPENDENCY_FILES:
        path = base_root / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return f"counterproof-sandbox:py{version}-{digest.hexdigest()[:12]}"


def _image_exists(docker: str, image: str, runner: Runner) -> bool:
    result = runner(
        [docker, "image", "inspect", image],
        cwd=Path.cwd(),
        env=dict(os.environ),
        timeout_s=PROBE_TIMEOUT_S,
    )
    return result.exit_code == 0


def build_python_image(base_root: Path, version: str, *, docker: str, runner: Runner) -> str:
    tag = python_image_tag(base_root, version)
    if _image_exists(docker, tag, runner):
        log.info("docker sandbox: reusing image %s", tag)
        return tag
    log.info("docker sandbox: building %s (installs the project's test dependencies)", tag)
    with tempfile.TemporaryDirectory(prefix="counterproof-image-") as tmp:
        context = Path(tmp)
        shutil.copytree(
            base_root,
            context / "project",
            ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "venv", "__pycache__"),
            symlinks=False,
        )
        (context / "Dockerfile").write_text(
            _DOCKERFILE_PY.format(version=version), encoding="utf-8"
        )
        result = runner(
            [docker, "build", "--quiet", "-t", tag, str(context)],
            cwd=context,
            env=dict(os.environ),
            timeout_s=BUILD_TIMEOUT_S,
        )
    if result.exit_code != 0:
        raise SandboxError(f"docker build failed: {(result.stderr or result.stdout)[-800:]}")
    return tag


def pull_node_image(major: str, *, docker: str, runner: Runner) -> str:
    image = f"node:{major}-slim"
    if not _image_exists(docker, image, runner):
        result = runner(
            [docker, "pull", "--quiet", image],
            cwd=Path.cwd(),
            env=dict(os.environ),
            timeout_s=BUILD_TIMEOUT_S,
        )
        if result.exit_code != 0:
            raise SandboxError(f"docker pull {image} failed: {result.stderr[-400:]}")
    return image


def _version(runner: Runner, executable: str, flag: str, pattern: str) -> str | None:
    try:
        result = runner(
            [executable, flag], cwd=Path.cwd(), env=dict(os.environ), timeout_s=PROBE_TIMEOUT_S
        )
    except OSError:
        return None
    found = re.search(pattern, result.stdout + result.stderr)
    return found.group(1) if found and result.exit_code == 0 else None


def attach(
    adapter: SandboxHost,
    *,
    stack: str,
    base_root: Path,
    run_dir: Path,
    deps_root: Path,
    executable: str | None,
    docker: str = "docker",
    runner: Runner = run_process,
    platform: str = sys.platform,
) -> DockerSandbox:
    """Build or pull the image for ``stack`` and route ``adapter``'s processes through it."""
    if platform == "win32":
        raise SandboxError(
            "the docker sandbox needs a Linux or macOS host (Docker Desktop on Windows cannot "
            "mount the run directory at the same path); run without --sandbox docker, or in WSL"
        )
    if shutil.which(docker) is None:
        raise SandboxError("docker is not installed or not on PATH")
    info = runner(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        cwd=Path.cwd(),
        env=dict(os.environ),
        timeout_s=PROBE_TIMEOUT_S,
    )
    if info.exit_code != 0:
        raise SandboxError(f"the docker daemon is not reachable: {info.stderr.strip()[-300:]}")
    read_only: list[Path] = []
    tools: dict[str, str] = {}
    if stack == "python":
        version = _version(runner, executable or "python3", "--version", r"Python (\d+\.\d+)")
        image = build_python_image(base_root, version or "3.12", docker=docker, runner=runner)
        if executable:
            tools[executable] = "python"
    else:
        if platform != "linux":
            raise SandboxError(
                "Node projects in the docker sandbox need a Linux host: the project's "
                "node_modules (mounted read-only) holds native modules built for this machine"
            )
        major = _version(runner, executable or "node", "--version", r"v(\d+)\.")
        image = pull_node_image(major or "24", docker=docker, runner=runner)
        if executable:
            tools[executable] = "node"
        modules = (deps_root / "node_modules").resolve()
        if modules.is_dir():
            read_only.append(modules)
    sandbox = DockerSandbox(
        image=image,
        mounts=[run_dir.resolve()],
        read_only=read_only,
        tools=tools,
        docker=docker,
        runner=runner,
    )
    adapter.sandbox = sandbox
    log.info("docker sandbox: %s (no network, 2 GB, 2 CPUs)", image)
    return sandbox


def docker_daemon_available(docker: str = "docker") -> bool:
    """For tests and ``doctor``: is a docker daemon reachable?"""
    if shutil.which(docker) is None:
        return False
    try:
        proc = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0
