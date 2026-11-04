from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from counterproof import doctor
from counterproof.cli import app
from counterproof.git import parse_version


def _status(checks: list[doctor.Check], name: str) -> str:
    return next(c.status for c in checks if c.name == name)


def test_doctor_passes_in_this_environment(tmp_path: Path) -> None:
    checks = doctor.run_checks(cwd=tmp_path, cache=tmp_path / "cache")
    assert doctor.passed(checks), checks
    assert _status(checks, "git") == "ok"
    assert _status(checks, "run directory") == "ok"


def test_doctor_fails_without_git(tmp_path: Path) -> None:
    checks = doctor.run_checks(cwd=tmp_path, cache=tmp_path / "cache", which=lambda _exe: None)
    assert _status(checks, "git") == "fail"
    assert _status(checks, "docker") == "warn"
    assert not doctor.passed(checks)


def test_doctor_fails_on_old_git(tmp_path: Path) -> None:
    checks = doctor.run_checks(
        cwd=tmp_path,
        cache=tmp_path / "cache",
        which=lambda exe: f"/usr/bin/{exe}",
        version_of=lambda _exe: "git version 2.10.1",
    )
    assert _status(checks, "git") == "fail"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git version 2.54.0.windows.1", (2, 54, 0)),
        ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
        ("git version 2.40", (2, 40, 0)),
        ("nonsense", None),
    ],
)
def test_parse_git_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert parse_version(text) == expected


def test_cli_doctor_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(doctor, "run_checks", lambda: [doctor.Check("git", "ok", "fine")])
    ok = runner.invoke(app, ["doctor"])
    assert ok.exit_code == 0, ok.output
    assert "all required checks passed" in ok.stdout

    monkeypatch.setattr(doctor, "run_checks", lambda: [doctor.Check("git", "fail", "missing")])
    bad = runner.invoke(app, ["doctor"])
    assert bad.exit_code == 1
    assert "required checks failed" in bad.stdout
