"""FR-WS-05 Docker sandbox: the container command, image reuse, refusals. A fake process
runner stands in for docker, so these tests need no daemon (the real container runs in
tests/integration/test_sandbox_docker.py)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from counterproof.workspace import sandbox_docker
from counterproof.workspace.sandbox_docker import (
    LIMITS,
    DockerSandbox,
    SandboxError,
    SandboxHost,
    attach,
    python_image_tag,
)
from counterproof.workspace.sandbox_none import ProcessResult


class FakeRunner:
    def __init__(self, replies: dict[str, ProcessResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.replies = replies or {}

    def __call__(self, command: Sequence[str], **_kw: Any) -> ProcessResult:
        cmd = [str(c) for c in command]
        self.calls.append(cmd)
        for key, reply in self.replies.items():
            if key in " ".join(cmd):
                return reply
        return _result(0)


def _result(code: int, out: str = "", *, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(
        command=[], exit_code=code, stdout=out, stderr="", duration_s=0.1,
        timed_out=timed_out, truncated=False,
    )  # fmt: skip


def test_fr_ws_05_container_command_isolates_and_keeps_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    sandbox = DockerSandbox(
        image="img:1",
        mounts=[run_dir],
        read_only=[tmp_path / "node_modules"],
        tools={"/home/u/.venv/bin/python": "python"},
        uid="1000:1000",
    )
    cmd = sandbox.command(
        ["/home/u/.venv/bin/python", "-m", "pytest", "-q"],
        cwd=run_dir / "ws" / "head",
        env={"PYTHONPATH": "/x/src", "PATH": "/host/bin", "HOME": "/home/u", "CI": "1"},
        name="counterproof-abc",
    )
    assert cmd[:6] == ["docker", "run", "--rm", "--init", "--name", "counterproof-abc"]
    joined = " ".join(cmd)
    assert " ".join(LIMITS) in joined and "--network none" in joined
    assert "--user 1000:1000" in joined
    assert f"-v {run_dir}:{run_dir}" in joined  # same path inside: every adapter path holds
    assert f"-v {tmp_path / 'node_modules'}:{tmp_path / 'node_modules'}:ro" in joined
    assert "-e PYTHONPATH=/x/src" in joined and "-e CI=1" in joined
    assert "PATH=/host/bin" not in joined and "HOME=/home/u" not in joined  # host-only env
    assert cmd[-5:] == ["img:1", "python", "-m", "pytest", "-q"]  # host interpreter mapped
    assert cmd[cmd.index("-w") + 1] == str(run_dir / "ws" / "head")


def test_fr_ws_05_a_timed_out_container_is_killed(tmp_path: Path) -> None:
    runner = FakeRunner({"docker run": _result(-9, timed_out=True)})
    sandbox = DockerSandbox(image="img", mounts=[tmp_path], runner=runner, uid="1:1")
    result = sandbox.run(["python", "-V"], cwd=tmp_path, env={}, timeout_s=5)
    assert result.timed_out and result.command == ["python", "-V"]
    run_cmd, kill_cmd = runner.calls
    name = run_cmd[run_cmd.index("--name") + 1]
    assert kill_cmd == ["docker", "kill", name]


def test_fr_ws_05_adapters_route_processes_through_the_sandbox(tmp_path: Path) -> None:
    runner = FakeRunner()
    host = SandboxHost()
    host.sandbox = DockerSandbox(image="img", mounts=[tmp_path], runner=runner, uid="1:1")
    host.run_process(["python", "-c", "1"], cwd=tmp_path, env={}, timeout_s=5)
    assert runner.calls and runner.calls[0][:2] == ["docker", "run"]


def test_fr_ws_05_python_image_is_keyed_by_dependency_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    first = python_image_tag(tmp_path, "3.12")
    assert first.startswith("counterproof-sandbox:py3.12-")
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    assert python_image_tag(tmp_path, "3.12") == first  # source changes reuse the image
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
    assert python_image_tag(tmp_path, "3.12") != first
    assert python_image_tag(tmp_path, "3.13") != python_image_tag(tmp_path, "3.12")


def _attach(tmp_path: Path, runner: FakeRunner, **kw: Any) -> tuple[SandboxHost, DockerSandbox]:
    host = SandboxHost()
    defaults: dict[str, Any] = {
        "stack": "python",
        "base_root": tmp_path,
        "run_dir": tmp_path / "run",
        "deps_root": tmp_path,
        "executable": "/venv/bin/python",
        "runner": runner,
        "platform": "linux",
    }
    sandbox = attach(host, **{**defaults, **kw})
    return host, sandbox


def test_fr_ws_05_attach_builds_the_python_image_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_docker.shutil, "which", lambda _name: "/usr/bin/docker")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    runner = FakeRunner({"--version": _result(0, "Python 3.12.7"), "image inspect": _result(1)})
    host, sandbox = _attach(tmp_path, runner)
    assert host.sandbox is sandbox and sandbox.image == python_image_tag(tmp_path, "3.12")
    assert sandbox.tools == {"/venv/bin/python": "python"}
    assert any(c[:2] == ["docker", "build"] for c in runner.calls)

    reuse = FakeRunner({"--version": _result(0, "Python 3.12.7")})  # inspect succeeds
    _attach(tmp_path, reuse)
    assert not any(c[:2] == ["docker", "build"] for c in reuse.calls)


def test_fr_ws_05_node_mounts_node_modules_read_only_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_docker.shutil, "which", lambda _name: "/usr/bin/docker")
    (tmp_path / "node_modules").mkdir()
    runner = FakeRunner({"--version": _result(0, "v24.3.0")})
    _, sandbox = _attach(tmp_path, runner, stack="node", executable="/usr/bin/node")
    assert sandbox.image == "node:24-slim"
    assert sandbox.read_only == [(tmp_path / "node_modules").resolve()]
    assert sandbox.tools == {"/usr/bin/node": "node"}


@pytest.mark.parametrize(
    ("platform", "stack", "message"),
    [
        ("win32", "python", "needs a Linux or macOS host"),
        ("darwin", "node", "Node projects in the docker sandbox need a Linux host"),
    ],
)
def test_fr_ws_05_unsupported_hosts_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, stack: str, message: str
) -> None:
    monkeypatch.setattr(sandbox_docker.shutil, "which", lambda _name: "/usr/bin/docker")
    with pytest.raises(SandboxError, match=message):
        _attach(tmp_path, FakeRunner(), platform=platform, stack=stack)


def test_fr_ws_05_missing_docker_or_daemon_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_docker.shutil, "which", lambda _name: None)
    with pytest.raises(SandboxError, match="not installed"):
        _attach(tmp_path, FakeRunner())
    monkeypatch.setattr(sandbox_docker.shutil, "which", lambda _name: "/usr/bin/docker")
    with pytest.raises(SandboxError, match="daemon is not reachable"):
        _attach(tmp_path, FakeRunner({"docker info": _result(1)}))
