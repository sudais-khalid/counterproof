"""FR-WS-04 environment scrubbing and FR-WS-06 hard timeouts with process-tree kill."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from counterproof.workspace.env import dropped_names, is_secret_name, scrub_env
from counterproof.workspace.sandbox_none import run_process

SOURCE = {
    "PATH": "/usr/bin",
    "HOME": "/home/dev",
    "SYSTEMROOT": r"C:\Windows",
    "LC_ALL": "C.UTF-8",
    "ANTHROPIC_API_KEY": "sk-secret",
    "GITHUB_TOKEN": "ghp_secret",
    "AWS_REGION": "eu-west-1",
    "DB_PASSWORD": "hunter2",
    "MY_SECRET": "x",
    "DATABASE_URL_TEST": "postgres://test",
    "LEDGER_MODE": "strict",
    "COUNTERPROOF_CACHE_DIR": "/cache",
    "RANDOM_TOOL_SETTING": "1",
}


def test_fr_ws_04_allowlist_only_by_default() -> None:
    env = scrub_env(SOURCE)
    assert set(env) == {"PATH", "HOME", "SYSTEMROOT", "LC_ALL"}


@pytest.mark.parametrize(
    "name",
    ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_REGION", "DB_PASSWORD", "MY_SECRET", "x_token"],
)
def test_fr_ws_04_secret_patterns(name: str) -> None:
    assert is_secret_name(name)


def test_fr_ws_04_explicit_pass_by_exact_name_even_if_secret() -> None:
    env = scrub_env(SOURCE, ["DATABASE_URL_TEST", "GITHUB_TOKEN"])
    assert env["DATABASE_URL_TEST"] == "postgres://test"
    assert env["GITHUB_TOKEN"] == "ghp_secret"
    assert "ANTHROPIC_API_KEY" not in env


def test_fr_ws_04_patterns_never_let_secrets_through() -> None:
    env = scrub_env(SOURCE, ["*"])
    assert "LEDGER_MODE" in env
    assert "RANDOM_TOOL_SETTING" in env
    for secret in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_REGION", "DB_PASSWORD", "MY_SECRET"):
        assert secret not in env


def test_no_counterproof_variables_reach_children_even_if_passed() -> None:
    """PRD 15.3: code under test must not be able to detect the verification run."""
    env = scrub_env(SOURCE, ["COUNTERPROOF_CACHE_DIR", "*"])
    assert not [k for k in env if k.upper().startswith("COUNTERPROOF_")]


def test_matching_is_case_insensitive() -> None:
    env = scrub_env({"Path": "x", "github_token": "y"}, [])
    assert env == {"Path": "x"}


def test_dropped_names_lists_names_only() -> None:
    kept = scrub_env(SOURCE)
    dropped = dropped_names(SOURCE, kept)
    assert "ANTHROPIC_API_KEY" in dropped
    assert "sk-secret" not in " ".join(dropped)


def test_fr_ws_04_secret_does_not_reach_a_real_child(tmp_path: Path) -> None:
    source = {**SOURCE, "PATH": os.environ["PATH"]}
    if sys.platform == "win32":
        source["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = run_process(
        [sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
        cwd=tmp_path,
        env=scrub_env(source),
        timeout_s=60,
    )
    assert result.exit_code == 0, result.stderr
    assert "ANTHROPIC_API_KEY" not in result.stdout
    assert "COUNTERPROOF_CACHE_DIR" not in result.stdout


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_fr_ws_06_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    result = run_process(
        [sys.executable, "-c", script], cwd=tmp_path, env=dict(os.environ), timeout_s=3
    )
    assert result.timed_out
    assert time.monotonic() - started < 60
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(child), "grandchild survived the timeout kill"


def test_output_is_truncated_at_the_limit(tmp_path: Path) -> None:
    result = run_process(
        [sys.executable, "-c", "print('x' * 5000)"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=60,
        output_limit=100,
    )
    assert result.truncated
    assert result.stdout.startswith("x" * 100)
    assert "output truncated" in result.stdout
    assert len(result.stdout) < 200


def test_exit_code_and_stderr_are_captured(tmp_path: Path) -> None:
    result = run_process(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=60,
    )
    assert (result.exit_code, result.stderr, result.timed_out) == (3, "boom", False)
