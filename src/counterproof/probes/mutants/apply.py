"""Applying and validating mutants (FR-MU-06)."""

from __future__ import annotations

import ast
import warnings

import libcst as cst

from counterproof.models import Mutant


class MutantMismatchError(ValueError):
    """The mutant's ``original`` text no longer matches the file it targets."""


def apply_mutant(text: str, mutant: Mutant) -> str:
    """Replace lines ``start_line..end_line`` (1-based, inclusive) with the replacement."""
    lines = text.splitlines(keepends=True)
    current = "".join(lines[mutant.start_line - 1 : mutant.end_line])
    if current != mutant.original:
        raise MutantMismatchError(
            f"{mutant.id}: lines {mutant.start_line}-{mutant.end_line} of {mutant.file} changed"
        )
    return (
        "".join(lines[: mutant.start_line - 1])
        + mutant.replacement
        + "".join(lines[mutant.end_line :])
    )


def _normalised(text: str) -> str | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.dump(ast.parse(text))
    except (SyntaxError, ValueError):
        return None


def invalid_reason(original_text: str, mutated_text: str, path: str) -> str | None:
    """Why a mutant must be rejected, or ``None`` if it is valid.

    Rejected when it does not parse, when its normalised syntax tree equals the original's
    (whitespace and comments are not part of the tree), or when it does not compile. Import
    failures are detected at run time and also mark the mutant invalid. TypeScript and
    JavaScript are checked with tree-sitter; their type errors are not (esbuild strips types).
    """
    from counterproof.probes import tsast  # noqa: PLC0415

    if tsast.is_ts_path(path):
        mutated_ts = tsast.normalised(path, mutated_text)
        if mutated_ts is None:
            return "does not parse"
        if mutated_ts == tsast.normalised(path, original_text):
            return "syntax tree unchanged"
        return None
    try:
        cst.parse_module(mutated_text)
    except cst.ParserSyntaxError:
        return "does not parse"
    mutated_tree = _normalised(mutated_text)
    if mutated_tree is None:
        return "does not compile"
    if mutated_tree == _normalised(original_text):
        return "syntax tree unchanged"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(mutated_text, path, "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        return "does not compile"
    return None
