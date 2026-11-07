"""Language dispatch for the probes: Python (libcst) or TypeScript/JavaScript (tree-sitter).

Every probe that reads source or test code calls these functions with the file's path, so
one code path serves pytest, Vitest and Jest repositories.
"""

from __future__ import annotations

from typing import Final

from counterproof.models import Finding
from counterproof.probes import pyast, tsast
from counterproof.probes.mutants.rule_forge_py import Candidate

PY_SUFFIXES: Final = (".py",)


def is_python(path: str) -> bool:
    return path.endswith(PY_SUFFIXES)


def is_typescript(path: str) -> bool:
    return tsast.is_ts_path(path)


def supported(path: str) -> bool:
    """Code Counterproof can analyse and mutate."""
    return is_python(path) or is_typescript(path)


def analyse_test_file(path: str, text: str) -> pyast.TestFile | None:
    if is_typescript(path):
        return tsast.analyse_test_file(text, path)
    return pyast.analyse_test_file(text)


def top_level_definitions(path: str, text: str) -> set[str]:
    if is_typescript(path):
        return tsast.top_level_definitions(text, path)
    return pyast.top_level_definitions(text)


def module_name(path: str) -> str:
    return tsast.module_name(path) if is_typescript(path) else pyast.module_name(path)


def added_symbols(path: str, base_text: str | None, head_text: str) -> set[str]:
    if is_typescript(path):
        return tsast.added_symbols(base_text, head_text, path)
    return pyast.added_symbols(base_text, head_text)


def overfit_findings(
    path: str, text: str, added_lines: set[int], test_literals: frozenset[pyast.Literal]
) -> list[Finding]:
    """FR-IN-07 for one changed source file."""
    if is_typescript(path):
        from counterproof.probes.integrity.overfit_ts import overfit_findings_ts  # noqa: PLC0415

        return overfit_findings_ts(path, text, added_lines, test_literals)
    from counterproof.probes.integrity.overfit import overfit_findings as py_findings  # noqa: PLC0415

    return py_findings(path, text, added_lines, test_literals)


def mutation_scope(path: str, text: str, changed_lines: set[int]) -> set[int]:
    if is_typescript(path):
        from counterproof.probes.mutants.rule_forge_ts import mutation_scope_ts  # noqa: PLC0415

        return mutation_scope_ts(path, text, changed_lines)
    from counterproof.probes.mutants.scope import mutation_scope as py_scope  # noqa: PLC0415

    return py_scope(text, changed_lines)


def rule_mutants(path: str, text: str, scope: set[int]) -> list[Candidate]:
    if is_typescript(path):
        from counterproof.probes.mutants.rule_forge_ts import generate_ts  # noqa: PLC0415

        return generate_ts(path, text, scope)
    from counterproof.probes.mutants.rule_forge_py import generate  # noqa: PLC0415

    return generate(path, text, scope)


def enclosing_function(path: str, text: str, line: int) -> str:
    if is_typescript(path):
        return tsast.enclosing_function(path, text, line)
    from counterproof.probes.mutants.equivalence import enclosing_function as py_fn  # noqa: PLC0415

    return py_fn(text, line)


def strip_comments(path: str, source: str) -> str:
    if is_typescript(path):
        return tsast.strip_comments(path, source)
    from counterproof.probes.mutants.equivalence import (  # noqa: PLC0415
        strip_comments_and_docstrings,
    )

    return strip_comments_and_docstrings(source)


def language_name(path: str) -> str:
    if is_typescript(path):
        return "javascript" if path.endswith(tsast.JS_SUFFIXES) else "typescript"
    return "python"
