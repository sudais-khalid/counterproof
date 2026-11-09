"""FR-WS-05 Docker sandbox end to end. The real-container test skips without a reachable
docker daemon locally, and fails instead of skipping where COUNTERPROOF_REQUIRE_DOCKER=1
(the Ubuntu CI jobs), so it can never be skipped silently there."""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from counterproof.cli import app
from counterproof.evidence.store import runs_root
from counterproof.models import RunManifest
from counterproof.workspace import sandbox_docker
from tests.helpers import build_repo

pytestmark = pytest.mark.integration
runner = CliRunner()
SEED = "00" * 16


def _verify(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> RunManifest:
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["verify", "--format", "json", "--seed", SEED, *args])
    assert result.exit_code in {0, 10, 20, 30, 40}, result.stderr
    return RunManifest.model_validate_json(result.stdout)


def test_fr_ws_05_a_sandbox_that_cannot_start_is_inconclusive_never_unsandboxed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_kw: object) -> None:
        raise sandbox_docker.SandboxError("the docker daemon is not reachable: test")

    monkeypatch.setattr(sandbox_docker, "attach", refuse)
    monkeypatch.setenv("COUNTERPROOF_CACHE_DIR", str(tmp_path / "cache"))
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant="honest").path
    manifest = _verify(repo, monkeypatch, "--sandbox", "docker")
    assert manifest.verdict.verdict == "INCONCLUSIVE"
    assert manifest.verdict.rule_id == "R-INC-BUILD"
    assert any("docker sandbox unavailable" in r for r in manifest.verdict.reasons)


def _docker_ready() -> bool:
    return sys.platform != "win32" and sandbox_docker.docker_daemon_available()


requires_docker = pytest.mark.skipif(
    not _docker_ready() and os.environ.get("COUNTERPROOF_REQUIRE_DOCKER") != "1",
    reason="no reachable docker daemon (CI sets COUNTERPROOF_REQUIRE_DOCKER=1)",
)


@requires_docker
@pytest.mark.parametrize("variant", ["honest", "hardcoded"])
def test_fr_ws_05_sandboxed_runs_give_the_same_verdict_inside_a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    monkeypatch.setenv("COUNTERPROOF_CACHE_DIR", str(tmp_path / "cache"))
    repo = build_repo(tmp_path / "repo", task="T1-discount-cap", variant=variant).path
    plain = _verify(repo, monkeypatch)
    boxed = _verify(repo, monkeypatch, "--sandbox", "docker")
    assert (boxed.verdict.verdict, boxed.verdict.rule_id) == (
        plain.verdict.verdict,
        plain.verdict.rule_id,
    )
    # The tests really ran in a container: pytest's JUnit report names its host.
    run_dir = next(runs_root().rglob(boxed.run_id))
    reports = sorted((run_dir / "junit").glob("*.xml"))
    assert reports
    hosts = {
        host
        for report in reports
        for host in re.findall(r'hostname="([^"]+)"', report.read_text(encoding="utf-8"))
    }
    assert hosts and socket.gethostname() not in hosts
