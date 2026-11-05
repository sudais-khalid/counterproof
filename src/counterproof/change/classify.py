"""File role classification (FR-CR-03).

Roles are decided in a fixed order: test, config, docs, source, other. Test conventions for
both Python and TypeScript/JavaScript are built in; adapters may add their own.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from typing import Final

from counterproof.models import FileRole

SOURCE_SUFFIXES: Final = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
)
DOC_SUFFIXES: Final = frozenset({".md", ".rst", ".adoc", ".txt"})
DOC_DIRS: Final = frozenset({"docs", "doc"})
TEST_DIRS: Final = frozenset({"tests", "test", "__tests__"})
TEST_FILE_GLOBS: Final = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.ts",
    "*.test.tsx",
    "*.test.js",
    "*.test.jsx",
    "*.test.mts",
    "*.test.mjs",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.mts",
    "*.spec.mjs",
)
CONFIG_NAMES: Final = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        "pytest.ini",
        "noxfile.py",
        ".coveragerc",
        "counterproof.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        "tsconfig.json",
        "Makefile",
        "Dockerfile",
    }
)
CONFIG_GLOBS: Final = (
    "requirements*.txt",
    "vitest.config.*",
    "vite.config.*",
    "jest.config.*",
    "tsconfig.*.json",
    "*.toml",
    "*.ini",
    "*.cfg",
    "*.yaml",
    "*.yml",
    ".*rc",
    ".*rc.json",
    ".*rc.js",
)

TestMatcher = Callable[[str], bool]


def is_test_path(path: str) -> bool:
    """Built-in test conventions: ``test_*.py``, ``*_test.py``, ``*.test.ts``, ``*.spec.ts``,
    ``__tests__/``, plus any source file under a ``tests``/``test`` directory (helpers and
    fixtures that a red-green transplant must carry along)."""
    pure = PurePosixPath(path)
    name = pure.name
    if any(fnmatch.fnmatchcase(name, g) for g in TEST_FILE_GLOBS):
        return True
    return pure.suffix in SOURCE_SUFFIXES and any(part in TEST_DIRS for part in pure.parts[:-1])


def classify(path: str, extra_test_matchers: Iterable[TestMatcher] = ()) -> FileRole:
    pure = PurePosixPath(path)
    name = pure.name
    if is_test_path(path) or any(m(path) for m in extra_test_matchers):
        return "test"
    if (
        name in CONFIG_NAMES
        or any(fnmatch.fnmatchcase(name, g) for g in CONFIG_GLOBS)
        or path.startswith(".github/")
    ):
        return "config"
    if pure.suffix.lower() in DOC_SUFFIXES or (
        pure.parts and pure.parts[0] in DOC_DIRS and pure.suffix not in SOURCE_SUFFIXES
    ):
        return "docs"
    if pure.suffix in SOURCE_SUFFIXES:
        return "source"
    return "other"
