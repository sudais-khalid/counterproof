"""Shared plumbing for the Node adapters (Vitest, Jest).

* ``node`` comes from ``COUNTERPROOF_NODE`` or ``PATH``.
* A worktree has no ``node_modules`` (it is never committed), so each worktree gets a link to
  the repository's own ``node_modules``. Nothing is ever installed (FR-AD-05).
* Test runners are started as ``node <package bin script>``, never through ``.cmd`` shims.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Final

from counterproof.log import get_logger
from counterproof.probes import tsast
from counterproof.workspace.env import scrub_env
from counterproof.workspace.links import is_dir_link, link_dir
from counterproof.workspace.manager import Workspace
from counterproof.workspace.sandbox_none import run_process

log = get_logger("adapters.node")

NODE_ENV_VAR: Final = "COUNTERPROOF_NODE"
PROBE_TIMEOUT_S: Final = 30.0
_RESOLVE_SUFFIXES: Final = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_REGEX_SPECIAL: Final = re.compile(r"([\\^$.*+?()\[\]{}|/])")


def resolve_node(env: Mapping[str, str]) -> str | None:
    configured = env.get(NODE_ENV_VAR)
    if configured:
        return configured
    return shutil.which("node", path=env.get("PATH"))


def read_package_json(root: Path) -> dict[str, object]:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def declared_dependencies(root: Path) -> set[str]:
    data = read_package_json(root)
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names |= {str(k) for k in section}
    return names


def package_bin(node_modules: Path, package: str, command: str) -> Path | None:
    """Path of ``command`` from ``package``'s ``bin`` field, inside ``node_modules``."""
    pkg_dir = node_modules / package
    data = read_package_json(pkg_dir)
    entry = data.get("bin")
    rel: object = entry.get(command) if isinstance(entry, dict) else entry
    if not isinstance(rel, str):
        return None
    path = pkg_dir / rel
    return path if path.is_file() else None


def ensure_node_modules(repo_root: Path, ws: Workspace) -> str | None:
    """Link the repository's ``node_modules`` into the worktree; an error message or None."""
    target = ws.root / "node_modules"
    if target.exists() or is_dir_link(target):
        return None
    source = repo_root / "node_modules"
    if not source.is_dir():
        return (
            f"{source} not found: install the project's dependencies first (for example "
            "`npm ci`). Counterproof does not install packages unless --allow-install is "
            "given (FR-AD-05)."
        )
    try:
        link_dir(source.resolve(), target)
    except OSError as exc:
        return f"cannot link node_modules into the worktree: {exc}"
    return None


def node_env(
    source: Mapping[str, str], passthrough: Sequence[str], node: str | None
) -> dict[str, str]:
    """FR-WS-04 scrubbed environment, with the directory of ``node`` first on ``PATH``."""
    env = scrub_env(source, passthrough)
    if node and Path(node).parent != Path():
        env["PATH"] = os.pathsep.join([str(Path(node).parent), env.get("PATH", "")])
    env.setdefault("CI", "true")  # no interactive or watch behaviour
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    return env


def node_version(node: str, env: Mapping[str, str], cwd: Path) -> str | None:
    try:
        proc = run_process([node, "--version"], cwd=cwd, env=env, timeout_s=PROBE_TIMEOUT_S)
    except OSError:
        return None
    return proc.stdout.strip() if proc.exit_code == 0 else None


def js_regex_escape(text: str) -> str:
    return _REGEX_SPECIAL.sub(r"\\\1", text)


def name_pattern(names: Sequence[str]) -> str:
    """Anchored regex matching exactly these full test names."""
    return "^(?:" + "|".join(js_regex_escape(n) for n in sorted(set(names))) + ")$"


def resolve_import(from_file: str, specifier: str, exists: Callable[[str], bool]) -> str | None:
    """Repo-relative path a relative import specifier points at, if it exists."""
    if not specifier.startswith("."):
        return None
    base = PurePosixPath(from_file).parent / specifier
    parts: list[str] = []
    for part in base.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    stem = "/".join(parts)
    stripped = re.sub(r"\.(?:js|mjs|cjs|jsx)$", "", stem)
    candidates = [stem]
    for root in dict.fromkeys([stem, stripped]):
        candidates += [root + s for s in _RESOLVE_SUFFIXES]
        candidates += [f"{root}/index{s}" for s in _RESOLVE_SUFFIXES]
    for candidate in candidates:
        if exists(candidate):
            return candidate
    return None


def related_by_imports(
    test_files: Sequence[str],
    src_files: Sequence[str],
    read: Callable[[str], str | None],
    max_depth: int = 6,
) -> set[str]:
    """Test files that import (transitively, through relative imports) a changed source file.

    Static equivalent of ``vitest related`` / ``jest --findRelatedTests`` that runs nothing.
    """
    wanted = set(src_files)
    cache: dict[str, list[str]] = {}

    def imports(path: str) -> list[str]:
        if path not in cache:
            text = read(path)
            specs = tsast.import_specifiers(text, path) if text is not None else []
            cache[path] = [
                p
                for s in specs
                if (p := resolve_import(path, s, lambda c: read(c) is not None)) is not None
            ]
        return cache[path]

    related: set[str] = set()
    for test_file in test_files:
        seen = {test_file}
        frontier = [test_file]
        for _ in range(max_depth):
            nxt: list[str] = []
            for path in frontier:
                for dep in imports(path):
                    if dep not in seen:
                        seen.add(dep)
                        nxt.append(dep)
            if seen & wanted:
                related.add(test_file)
                break
            frontier = nxt
            if not frontier:
                break
    return related
