"""Mutation scope (FR-MU-01).

Only lines the change added or modified in head, plus at most two lines of immediate
structural context per changed block: the condition of the innermost enclosing ``if``/``while``
and the first ``return`` after the block in the same function. Tests are never mutated.
"""

from __future__ import annotations

from collections.abc import Mapping

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

from counterproof.probes.pyast import parse

MAX_CONTEXT_LINES = 2


def _blocks(lines: set[int]) -> list[tuple[int, int]]:
    """Contiguous runs of line numbers, as ``(first, last)``."""
    runs: list[tuple[int, int]] = []
    for n in sorted(lines):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


class _Structure(cst.CSTVisitor):
    def __init__(self, positions: Mapping[cst.CSTNode, CodeRange]) -> None:
        self.positions = positions
        self.conditions: list[tuple[int, int, int]] = []
        """(test line, body first line, body last line) for if/elif/while."""
        self.functions: list[tuple[int, int]] = []
        self.returns: list[int] = []

    def _span(self, node: cst.CSTNode) -> tuple[int, int]:
        pos = self.positions[node]
        return pos.start.line, pos.end.line

    def visit_If(self, node: cst.If) -> None:
        body_start, body_end = self._span(node.body)
        self.conditions.append((self.positions[node.test].start.line, body_start, body_end))

    def visit_While(self, node: cst.While) -> None:
        body_start, body_end = self._span(node.body)
        self.conditions.append((self.positions[node.test].start.line, body_start, body_end))

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.functions.append(self._span(node))

    def visit_Return(self, node: cst.Return) -> None:
        self.returns.append(self.positions[node].start.line)


def mutation_scope(head_text: str, changed_lines: set[int]) -> set[int]:
    """Lines RuleForge may mutate in one source file."""
    module = parse(head_text)
    if module is None or not changed_lines:
        return set(changed_lines)
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    structure = _Structure(wrapper.resolve(PositionProvider))
    wrapper.module.visit(structure)

    scope = set(changed_lines)
    for first, last in _blocks(changed_lines):
        context: list[int] = []
        enclosing = [
            test for test, start, end in structure.conditions if start <= first and last <= end
        ]
        if enclosing:
            condition = max(enclosing)  # innermost
            if condition not in changed_lines:
                context.append(condition)
        functions = [(s, e) for s, e in structure.functions if s <= first and last <= e]
        if functions:
            fn_start, fn_end = max(functions)  # innermost
            following = [r for r in structure.returns if last < r <= fn_end and r > fn_start]
            if following and min(following) not in changed_lines:
                context.append(min(following))
        scope.update(context[:MAX_CONTEXT_LINES])
    return scope
