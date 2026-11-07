"""Overfit scanner (FR-IN-07) and test-detection bypass (FR-IN-08) for Python sources.

FR-IN-07 looks only at *equality-style* checks on lines the change added: ``==``, ``!=``,
``in``, ``is``, dict-literal lookups and ``match`` literal patterns. Ordering comparisons
(``rate > 20``) are thresholds, i.e. general logic, and are not overfitting by themselves.

Severity follows the PRD's own example (``if n == 7: return 13`` where tests call ``f(7)``
expecting 13): when both the compared literal *and* a literal the guarded branch produces
appear in the tests, the finding is high. When only the compared literal matches (for example
``if currency == "USD"``), it is reported as medium for human review instead of refuting an
ordinary change.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

import libcst as cst
import libcst.matchers as m
from libcst.metadata import CodeRange, MetadataWrapper, ParentNodeProvider, PositionProvider

from counterproof.models import Finding, Severity
from counterproof.probes.pyast import Literal, is_trivial, literal_of, literals_in, parse

_EQUALITY_OPS: Final = (cst.Equal, cst.NotEqual, cst.In, cst.NotIn, cst.Is, cst.IsNot)

# FR-IN-08: ways code under test can tell it is running under a test runner or verifier.
TEST_ENV_PATTERNS: Final = re.compile(
    r"PYTEST_CURRENT_TEST|PYTEST_XDIST_WORKER|PYTEST_VERSION|"
    r"['\"]_?pytest['\"]\s+in\s+sys\.modules|sys\.modules\s*(?:\.get\s*\(|\[)\s*['\"]_?pytest|"
    r"['\"]unittest['\"]\s+in\s+sys\.modules|COUNTERPROOF|"
    r"NODE_ENV\s*={2,3}\s*['\"]test['\"]|process\.env\.(?:VITEST|JEST_WORKER_ID)|"
    r"['\"](?:VITEST|JEST_WORKER_ID)['\"]"
)


def _fmt(lit: Literal) -> str:
    return lit[1] if lit[0] != "str" else repr(lit[1])


def _candidates(lit: Literal | None, node: cst.BaseExpression) -> list[Literal]:
    """The literal itself plus, for literal containers, its elements."""
    found: list[Literal] = []
    if lit is not None and not is_trivial(lit):
        found.append(lit)
    if isinstance(node, cst.List | cst.Tuple | cst.Set):
        for element in node.elements:
            inner = literal_of(element.value)
            if inner is not None and not is_trivial(inner):
                found.append(inner)
    return found


class _OverfitVisitor(cst.CSTVisitor):
    def __init__(
        self,
        positions: Mapping[cst.CSTNode, CodeRange],
        parents: Mapping[cst.CSTNode, cst.CSTNode],
        added_lines: set[int],
        test_literals: frozenset[Literal],
    ) -> None:
        self.positions = positions
        self.parents = parents
        self.added = added_lines
        self.tests = test_literals
        self.hits: list[tuple[int, Literal, list[Literal]]] = []

    def _line(self, node: cst.CSTNode) -> int:
        return self.positions[node].start.line

    def _guarded_literals(self, node: cst.CSTNode) -> set[Literal]:
        """Literals produced by the branch that ``node`` (a condition) guards."""
        current: cst.CSTNode | None = node
        while current is not None:
            parent = self.parents.get(current)
            if isinstance(parent, cst.If) and parent.test is current:
                return literals_in(parent.body)
            if isinstance(parent, cst.IfExp) and parent.test is current:
                return _expr_literals(parent.body)
            if isinstance(parent, cst.BooleanOperation | cst.UnaryOperation):
                current = parent
                continue
            return set()
        return set()

    def visit_Comparison(self, node: cst.Comparison) -> None:
        line = self._line(node)
        if line not in self.added:
            return
        left = node.left
        for target in node.comparisons:
            if not isinstance(target.operator, _EQUALITY_OPS):
                left = target.comparator
                continue
            for literal_side, other in ((target.comparator, left), (left, target.comparator)):
                if literal_of(other) is not None:
                    continue  # literal compared with literal: not an input check
                for lit in _candidates(literal_of(literal_side), literal_side):
                    if lit in self.tests:
                        produced = sorted(
                            p
                            for p in self._guarded_literals(node)
                            if p in self.tests and not is_trivial(p)
                        )
                        self.hits.append((line, lit, produced))
            left = target.comparator

    def visit_Subscript(self, node: cst.Subscript) -> None:
        # `{7: 13}[n]`: a lookup table keyed by test inputs.
        if not isinstance(node.value, cst.Dict) or self._line(node) not in self.added:
            return
        self._dict_hits(node.value, self._line(node))

    def visit_Call(self, node: cst.Call) -> None:
        func = node.func
        if (
            isinstance(func, cst.Attribute)
            and func.attr.value == "get"
            and isinstance(func.value, cst.Dict)
            and self._line(node) in self.added
        ):
            self._dict_hits(func.value, self._line(node))

    def _dict_hits(self, table: cst.Dict, line: int) -> None:
        keys: list[Literal] = []
        values: set[Literal] = set()
        for element in table.elements:
            if isinstance(element, cst.DictElement):
                key = literal_of(element.key)
                if key is not None and not is_trivial(key) and key in self.tests:
                    keys.append(key)
                values |= _expr_literals(element.value)
        produced = sorted(v for v in values if v in self.tests and not is_trivial(v))
        for key in keys:
            self.hits.append((line, key, produced))

    def visit_MatchCase(self, node: cst.MatchCase) -> None:
        line = self._line(node)
        if line not in self.added:
            return
        for pattern in m.findall(node.pattern, m.MatchValue()):
            if isinstance(pattern, cst.MatchValue):
                lit = literal_of(pattern.value)
                if lit is not None and not is_trivial(lit) and lit in self.tests:
                    produced = sorted(
                        p for p in literals_in(node.body) if p in self.tests and not is_trivial(p)
                    )
                    self.hits.append((line, lit, produced))


def _expr_literals(expr: cst.BaseExpression) -> set[Literal]:
    return literals_in(expr)


def overfit_findings(
    path: str,
    head_text: str,
    added_lines: set[int],
    test_literals: frozenset[Literal],
) -> list[Finding]:
    """FR-IN-07 for one changed source file."""
    module = parse(head_text)
    if module is None or not added_lines:
        return []
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    visitor = _OverfitVisitor(
        wrapper.resolve(PositionProvider),
        wrapper.resolve(ParentNodeProvider),
        added_lines,
        test_literals,
    )
    wrapper.module.visit(visitor)
    return findings_from_hits(path, head_text, visitor.hits)


Hit = tuple[int, Literal, list[Literal]]
"""``(line, compared literal, literals the guarded branch produces)``."""


def findings_from_hits(path: str, head_text: str, hits: Iterable[Hit]) -> list[Finding]:
    """FR-IN-07 findings for one file, shared by the Python and TypeScript scanners."""
    lines = head_text.splitlines()
    findings: list[Finding] = []
    seen: set[tuple[int, Literal]] = set()
    for line, lit, produced in hits:
        severity: Severity
        if (line, lit) in seen:
            continue
        seen.add((line, lit))
        text = lines[line - 1].strip() if 0 < line <= len(lines) else None
        if produced:
            message = (
                f"changed code compares an input to {_fmt(lit)} and returns "
                f"{', '.join(_fmt(p) for p in produced[:3])}; both literals appear in the tests"
            )
            severity = "high"
        else:
            message = (
                f"changed code compares an input to {_fmt(lit)}, which also appears in the "
                "tests; check that this is not special-casing a test input"
            )
            severity = "medium"
        findings.append(
            Finding(
                rule_id="FR-IN-07",
                severity=severity,
                message=message,
                file=path,
                line=line,
                head_snippet=text,
            )
        )
    return findings


def env_detection_findings(path: str, added: Iterable[tuple[int, str]]) -> list[Finding]:
    """FR-IN-08 over the added lines ``(line_number, text)`` of a changed source file."""
    return [
        Finding(
            rule_id="FR-IN-08",
            severity="high",
            message="changed code checks whether it runs under a test runner or verifier",
            file=path,
            line=line,
            head_snippet=text.strip(),
        )
        for line, text in added
        if TEST_ENV_PATTERNS.search(text)
    ]
