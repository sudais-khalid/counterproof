"""Self-configuration change detection (FR-CR-06, feeds FR-IN-09).

A change is flagged ``self_config_change`` when it edits Counterproof's own config, a CI
workflow that invokes Counterproof, or test-runner configuration. Agents that cannot make
tests pass sometimes reconfigure the runner instead; this makes that visible.
"""

from __future__ import annotations

import configparser
import fnmatch
import json
import tomllib
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import Any, Final

from counterproof.models import ChangedFile

SELF_CONFIG_FLAG: Final = "self_config_change"

ALWAYS_RUNNER_CONFIG: Final = (
    "pytest.ini",
    "vitest.config.*",
    "vitest.workspace.*",
    "jest.config.*",
    "counterproof.toml",
)

Reader = Callable[[str], str | None]


def _pyproject_runner_tables(text: str | None) -> Any:
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return text  # unparseable: compare raw text
    tool = data.get("tool", {})
    return {k: tool.get(k) for k in ("pytest", "counterproof", "coverage")}


def _ini_sections(text: str | None, sections: Sequence[str]) -> Any:
    if text is None:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return text
    return {s: dict(parser.items(s)) if parser.has_section(s) else None for s in sections}


def _package_json_runner(text: str | None) -> Any:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    test_script = scripts.get("test") if isinstance(scripts, dict) else None
    return {"jest": data.get("jest"), "vitest": data.get("vitest"), "test": test_script}


def _mentions_counterproof(text: str | None) -> bool:
    return text is not None and "counterproof" in text.lower()


def _differs(base: object, head: object) -> bool:
    return bool(base != head)


def file_is_self_config(path: str, read_base: Reader, read_head: Reader) -> bool:
    name = PurePosixPath(path).name
    if any(fnmatch.fnmatchcase(name, g) for g in ALWAYS_RUNNER_CONFIG):
        return True
    if path.startswith(".github/workflows/"):
        return _mentions_counterproof(read_base(path)) or _mentions_counterproof(read_head(path))
    if name == "pyproject.toml":
        return _differs(
            _pyproject_runner_tables(read_base(path)), _pyproject_runner_tables(read_head(path))
        )
    if name == "setup.cfg":
        sections = ("tool:pytest", "coverage:run")
        return _differs(
            _ini_sections(read_base(path), sections), _ini_sections(read_head(path), sections)
        )
    if name == "tox.ini":
        return _differs(
            _ini_sections(read_base(path), ("pytest",)), _ini_sections(read_head(path), ("pytest",))
        )
    if name == ".coveragerc":
        return True
    if name == "package.json":
        return _differs(
            _package_json_runner(read_base(path)), _package_json_runner(read_head(path))
        )
    return False


def self_config_paths(
    files: Sequence[ChangedFile], read_base: Reader, read_head: Reader
) -> list[str]:
    return sorted(
        f.path
        for f in files
        if file_is_self_config(f.path, read_base, read_head)
        or (f.old_path is not None and file_is_self_config(f.old_path, read_base, read_head))
    )
