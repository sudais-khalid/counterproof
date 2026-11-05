"""Local (no container) process runner: hard timeouts and process-tree kill (FR-WS-06).

Output is captured through temporary files rather than pipes, so a chatty test suite cannot
deadlock us, and only the first ``output_limit`` bytes of each stream are kept (PRD 15.1).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Final

OUTPUT_LIMIT: Final = 2 * 1024 * 1024
_TRUNCATION_NOTE: Final = "\n[counterproof: output truncated]\n"


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    truncated: bool


def _read_capped(fh: IO[bytes], limit: int) -> tuple[str, bool]:
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(0)
    data = fh.read(limit)
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        return text + _TRUNCATION_NOTE, True
    return text, False


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the process and everything it spawned."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    output_limit: int = OUTPUT_LIMIT,
) -> ProcessResult:
    """Run ``command`` with a hard timeout; on timeout kill the whole process tree."""
    cmd = [str(c) for c in command]
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        start = time.monotonic()
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            **kwargs,
        )
        timed_out = False
        try:
            exit_code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            exit_code = proc.wait()
        except BaseException:
            # KeyboardInterrupt or SIGTERM: never leave the child running.
            _kill_tree(proc)
            proc.wait()
            raise
        duration = time.monotonic() - start
        stdout, cut_out = _read_capped(out, output_limit)
        stderr, cut_err = _read_capped(err, output_limit)
    return ProcessResult(
        command=cmd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration,
        timed_out=timed_out,
        truncated=cut_out or cut_err,
    )
