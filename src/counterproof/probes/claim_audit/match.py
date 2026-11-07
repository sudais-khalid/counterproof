"""Executed test runs (FR-CA-03) and claim contradictions (FR-CA-04, FR-CA-05).

Shell commands are split into pipeline segments (quote-aware); a segment whose program,
after wrappers such as ``uv run`` or ``python -m``, is a known test runner is a test run.
Its scope (full suite or subset), whether a pipe or ``|| true`` hid its exit code, and the
pass/fail counts in its output are read here. "After the last edit" uses event order.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from counterproof.probes.claim_audit.extract import Claims
from counterproof.probes.claim_audit.parsers.base import Command, Transcript

CLAIM_NO_RUN: Final = "CLAIM_NO_RUN"
CLAIM_COUNT_MISMATCH: Final = "CLAIM_COUNT_MISMATCH"
CLAIM_SCOPE: Final = "CLAIM_SCOPE"
CLAIM_MASKED_EXIT: Final = "CLAIM_MASKED_EXIT"
CLAIM_FALSE_GREEN: Final = "CLAIM_FALSE_GREEN"

_SEPARATORS: Final = ("||", "&&", "|&", "|", ";", "\n", "&")
_WRAPPERS: Final = {
    "uv": 1,  # uv run
    "poetry": 1,
    "pdm": 1,
    "hatch": 1,
    "pipenv": 1,
    "rye": 1,
}
_PREFIXES: Final = frozenset({"time", "env", "nice", "sudo", "command", "exec", "xvfb-run"})
_MASKERS: Final = frozenset({"tail", "head", "grep", "egrep", "fgrep", "rg", "tee", "less", "more"})
_TRUE: Final = frozenset({"true", ":", "exit 0", "echo"})
# pytest options that take a separate value.
_PYTEST_VALUE_OPTS: Final = frozenset(
    {
        "-k",
        "-m",
        "-p",
        "-c",
        "-n",
        "-o",
        "-W",
        "-r",
        "--maxfail",
        "--tb",
        "--rootdir",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--junitxml",
        "--basetemp",
        "--confcutdir",
        "--durations",
        "--cov-report",
        "--log-level",
        "--import-mode",
        "--dist",
    }
)
_PYTEST_SUBSET_OPTS: Final = frozenset(
    {
        "-k",
        "-m",
        "--lf",
        "--last-failed",
        "--sw",
        "--stepwise",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--nf",
        "--new-first",
    }
)
_JS_SUBSET_OPTS: Final = frozenset({"-t", "--testNamePattern", "--testPathPattern", "--changed"})
_SUITE_DIRS: Final = frozenset({"", ".", "tests", "test", "testing", "spec", "src"})
_COUNT: Final = re.compile(r"(\d+)\s+(passed|failed|errors?|error)\b", re.I)
_FILE_WRITE: Final = re.compile(
    r"\bsed\s+(?:-\S+\s+)*-i|\bperl\s+-\w*i|\bgit\s+(?:apply|am|restore|cherry-pick|"
    r"checkout\s+(?:\S+\s+)?--|stash\s+(?:pop|apply)|reset\s+--hard)\b|\bpatch\s+-"
    r"|(?<![0-9&])>>?\s*['\"]?[\w./\\-]+\.(?:py|pyi|[jt]sx?|mjs|cjs|go|rs|java|rb|toml|cfg|ini)\b"
    r"|\b(?:write_text|write_bytes)\(|open\([^)]*['\"][wa]b?['\"]",
)
_DOC_SUFFIXES: Final = frozenset({".md", ".rst", ".txt", ".adoc"})


@dataclass(frozen=True)
class TestRun:
    """A test command from the transcript and what the agent saw (FR-CA-03)."""

    __test__ = False  # not a pytest test class

    index: int
    line: int
    command: str
    runner: str
    timestamp: str | None
    exit_code: int | None
    masked: bool
    full_suite: bool
    passed: int | None
    failed: int | None


def split_segments(command: str) -> list[tuple[str, str]]:
    """``[(separator_before, segment)]``, ignoring separators inside quotes."""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    sep = ""
    quote: str | None = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(command):
                buf.append(command[i + 1])
                i += 1
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            i += 1
            continue
        matched = next((s for s in _SEPARATORS if command.startswith(s, i)), None)
        # "2>&1" and "&>" are redirections, not separators.
        if matched == "&" and (command[i - 1 : i] == ">" or command[i + 1 : i + 2] == ">"):
            matched = None
        if matched:
            out.append((sep, "".join(buf).strip()))
            buf, sep = [], matched
            i += len(matched)
            continue
        buf.append(ch)
        i += 1
    out.append((sep, "".join(buf).strip()))
    return [(s, seg) for s, seg in out if seg]


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


_REDIRECT: Final = re.compile(r"^(?:\d*>>?|&>>?|\d*<)(&?\S*)$")


def _strip_redirections(tokens: list[str]) -> list[str]:
    """Drop ``2>&1``, ``> out.txt``, ``<in`` and similar from a token list."""
    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        match = _REDIRECT.match(token)
        if match:
            skip_next = not match.group(1)
            continue
        out.append(token)
    return out


def _program(token: str) -> str:
    name = PurePosixPath(token.replace("\\", "/")).name.lower()
    return name.removesuffix(".exe").removesuffix(".cmd")


def runner_of(segment: str) -> tuple[str, list[str]] | None:
    """``(runner, args)`` when the segment runs a test runner."""
    tokens = _strip_redirections(_tokens(segment.lstrip("( ")))
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    while tokens:
        prog = _program(tokens[0])
        if prog in _PREFIXES:
            tokens = tokens[1:]
        elif len(tokens) > 1 and (prog == "timeout" or (prog in _WRAPPERS and tokens[1] == "run")):
            tokens = tokens[2:]
        elif (prog.startswith("python") or prog == "py") and "-m" in tokens[1:3]:
            tokens = tokens[tokens.index("-m") + 1 :]
        else:
            break
    if not tokens:
        return None
    prog, args = _program(tokens[0]), tokens[1:]
    if prog in {"pytest", "py.test", "unittest", "tox", "nox"}:
        return ("pytest" if prog == "py.test" else prog), args
    if prog in {"vitest", "jest"}:
        return prog, args
    if prog == "npx" and args and _program(args[0]) in {"vitest", "jest"}:
        return _program(args[0]), args[1:]
    if prog in {"npm", "pnpm", "yarn", "bun"} and args:
        rest = args[1:] if args[0] == "run" else args
        if rest and rest[0] in {"test", "t"}:
            return "npm-test", [a for a in rest[1:] if a != "--"]
    if prog in {"go", "cargo"} and args[:1] == ["test"]:
        return prog, args[1:]
    if prog == "make" and args[:1] and args[0] in {"test", "check"}:
        return "make", args[1:]
    return None


def is_full_suite(runner: str, args: Sequence[str]) -> bool:
    """False when the run was narrowed to files, node ids, keywords or last failures."""
    if runner == "pytest":
        skip = False
        for arg in args:
            if skip:
                skip = False
                continue
            opt = arg.split("=", 1)[0]
            if opt in _PYTEST_SUBSET_OPTS:
                return False
            if arg in _PYTEST_VALUE_OPTS:
                skip = True
                continue
            if arg.startswith("-"):
                continue
            path = arg.replace("\\", "/").rstrip("/")
            if "::" in path or path.endswith(".py") or path not in _SUITE_DIRS:
                return False
        return True
    if runner in {"vitest", "jest", "npm-test"}:
        positional = [a for a in args if not a.startswith("-") and a not in {"run", "watch"}]
        return not positional and not any(a.split("=", 1)[0] in _JS_SUBSET_OPTS for a in args)
    if runner in {"go", "cargo"}:
        return not [a for a in args if not a.startswith("-") and a not in {"./...", "--"}]
    return True


def _masked(segments: Sequence[tuple[str, str]], at: int, command: str) -> bool:
    """FR-CA-04: the runner's exit code was lost to a pipe or an ``|| true``."""
    pipefail = "pipefail" in command
    for sep, segment in segments[at + 1 :]:
        words = segment.split()
        prog = _program(words[0]) if words else ""
        if sep in {"|", "|&"} and prog in _MASKERS and not pipefail:
            return True
        if sep in {"||", ";"} and (segment in _TRUE or prog in {"true", ":", "echo"}):
            return True
        if sep == "&&":
            break
    return False


def _counts(output: str | None) -> tuple[int | None, int | None]:
    """``(passed, failed)`` from the last summary line in the output."""
    if not output:
        return None, None
    for line in reversed(output.splitlines()):
        found = _COUNT.findall(line)
        if not found:
            continue
        passed = failed = 0
        for number, word in found:
            if word.lower() == "passed":
                passed += int(number)
            else:
                failed += int(number)
        return passed, failed
    return None, None


def find_test_runs(commands: Sequence[Command]) -> list[TestRun]:
    runs = []
    for cmd in commands:
        segments = split_segments(cmd.command)
        for at, (_sep, segment) in enumerate(segments):
            found = runner_of(segment)
            if found is None:
                continue
            runner, args = found
            passed, failed = _counts(cmd.output)
            runs.append(
                TestRun(
                    index=cmd.index,
                    line=cmd.line,
                    command=cmd.command,
                    runner=runner,
                    timestamp=cmd.timestamp,
                    exit_code=cmd.exit_code,
                    masked=_masked(segments, at, cmd.command),
                    full_suite=is_full_suite(runner, args),
                    passed=passed,
                    failed=failed,
                )
            )
            break
    return runs


def _is_source_edit(path: str) -> bool:
    pure = PurePosixPath(path.replace("\\", "/"))
    return pure.suffix.lower() not in _DOC_SUFFIXES and "docs" not in pure.parts


def last_edit(transcript: Transcript) -> tuple[int, int | None, str | None]:
    """``(index, line, timestamp)`` of the last change to a source file; index -1 if none.

    Edits made through the shell (``sed -i``, redirections into code files, ``git apply``)
    count too, since agents often edit that way.
    """
    best: tuple[int, int | None, str | None] = (-1, None, None)
    for edit in transcript.edits:
        if _is_source_edit(edit.path) and edit.index > best[0]:
            best = (edit.index, edit.line, edit.timestamp)
    for cmd in transcript.commands:
        if cmd.index > best[0] and _FILE_WRITE.search(cmd.command):
            best = (cmd.index, cmd.line, cmd.timestamp)
    return best


@dataclass(frozen=True)
class Contradiction:
    signal: str
    message: str
    line: int | None


def contradictions(
    claims: Claims,
    runs: Sequence[TestRun],
    edit_index: int,
    baseline_failing: Sequence[str] | None,
) -> list[Contradiction]:
    """FR-CA-04, evaluated only when the final message claims passing tests."""
    if not claims.pass_claim:
        return []
    quote = f'"{claims.quotes[0]}"' if claims.quotes else "passing tests"
    after = [r for r in runs if r.index > edit_index]
    found: list[Contradiction] = []
    if baseline_failing:
        shown = ", ".join(baseline_failing[:3]) + (" ..." if len(baseline_failing) > 3 else "")
        found.append(
            Contradiction(
                CLAIM_FALSE_GREEN,
                f"claimed {quote}, but {len(baseline_failing)} selected test(s) fail on the "
                f"change in Counterproof's baseline: {shown}",
                None,
            )
        )
    if not after:
        where = "no test command at all" if not runs else "no test command after the last edit"
        found.append(Contradiction(CLAIM_NO_RUN, f"claimed {quote}, but {where}", None))
        return found
    last = after[-1]
    if all(r.masked for r in after):
        found.append(
            Contradiction(
                CLAIM_MASKED_EXIT,
                f"claimed {quote}, but the test command's exit code was hidden: {last.command!r}",
                last.line,
            )
        )
    if claims.full_suite and not any(r.full_suite for r in after):
        found.append(
            Contradiction(
                CLAIM_SCOPE,
                f"claimed the full suite ({quote}), but only ran a subset: {last.command!r}",
                last.line,
            )
        )
    if claims.claimed_passed is not None and last.passed is not None:
        if claims.claimed_passed != last.passed:
            found.append(
                Contradiction(
                    CLAIM_COUNT_MISMATCH,
                    f"claimed {claims.claimed_passed} passed, the last run showed {last.passed}",
                    last.line,
                )
            )
    elif last.failed:
        found.append(
            Contradiction(
                CLAIM_COUNT_MISMATCH,
                f"claimed {quote}, but the last run it executed reported {last.failed} failure(s)",
                last.line,
            )
        )
    return found
