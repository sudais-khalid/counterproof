"""Overfit scanner (FR-IN-07) for TypeScript and JavaScript sources (tree-sitter).

Same policy as the Python scanner: only equality-style checks on added lines count
(``===``, ``==``, ``!==``, ``!=``, ``switch`` cases, object-literal lookups, ``.includes``
on array literals and ``new Map([...]).get``). Ordering comparisons are thresholds.
"""

from __future__ import annotations

from typing import Final

from tree_sitter import Node

from counterproof.models import Finding
from counterproof.probes.integrity.overfit import Hit, findings_from_hits
from counterproof.probes.pyast import Literal, is_trivial
from counterproof.probes.tsast import (
    call_args,
    line_of,
    literal_of,
    literals_in,
    parse,
    text_of,
    walk,
)

_EQUALITY: Final = frozenset({"===", "==", "!==", "!="})
_PASS_THROUGH: Final = frozenset(
    {"binary_expression", "unary_expression", "parenthesized_expression"}
)


_WRAPPERS: Final = frozenset(
    {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression"}
)


def _unwrap(node: Node | None) -> Node | None:
    """Look through parentheses and type assertions: ``({...} as Record<K, V>)``."""
    while node is not None and node.type in _WRAPPERS:
        node = node.named_children[0] if node.named_children else None
    return node


def _candidates(node: Node) -> list[Literal]:
    found: list[Literal] = []
    lit = literal_of(node)
    if lit is not None and not is_trivial(lit):
        found.append(lit)
    if node.type == "array":
        for element in node.named_children:
            inner = literal_of(element)
            if inner is not None and not is_trivial(inner):
                found.append(inner)
    return found


class _Scanner:
    def __init__(self, added: set[int], tests: frozenset[Literal]) -> None:
        self.added = added
        self.tests = tests
        self.hits: list[Hit] = []

    def _produced(self, literals: set[Literal]) -> list[Literal]:
        return sorted(p for p in literals if p in self.tests and not is_trivial(p))

    def _guarded(self, node: Node) -> set[Literal]:
        """Literals produced by the branch that ``node`` (part of a condition) guards."""
        current = node
        while current.parent is not None:
            parent = current.parent
            if parent.type in {"if_statement", "ternary_expression"}:
                cond = parent.child_by_field_name("condition")
                if cond is not None and cond.id == current.id:
                    body = parent.child_by_field_name("consequence")
                    return literals_in(body) if body is not None else set()
                return set()
            if parent.type in _PASS_THROUGH:
                current = parent
                continue
            return set()
        return set()

    def comparison(self, node: Node) -> None:
        if text_of(node.child_by_field_name("operator")) not in _EQUALITY:
            return
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None or right is None:
            return
        for literal_side, other in ((right, left), (left, right)):
            if literal_of(other) is not None:
                continue  # literal compared with literal: not an input check
            for lit in _candidates(literal_side):
                if lit in self.tests:
                    self.hits.append((line_of(node), lit, self._produced(self._guarded(node))))

    def switch_case(self, node: Node) -> None:
        value = node.child_by_field_name("value")
        lit = literal_of(value) if value is not None else None
        if lit is None or is_trivial(lit) or lit not in self.tests:
            return
        produced: set[Literal] = set()
        for child in node.named_children:
            if value is None or child.id != value.id:
                produced |= literals_in(child)
        self.hits.append((line_of(node), lit, self._produced(produced)))

    def table(self, table: Node, line: int) -> None:
        keys: list[Literal] = []
        values: set[Literal] = set()
        for pair in table.named_children:
            if pair.type != "pair":
                continue
            key_node = pair.child_by_field_name("key")
            value_node = pair.child_by_field_name("value")
            key = literal_of(key_node) if key_node is not None else None
            if key is None and key_node is not None and key_node.type == "property_identifier":
                key = ("str", text_of(key_node))
            if key is not None and not is_trivial(key) and key in self.tests:
                keys.append(key)
            if value_node is not None:
                values |= literals_in(value_node)
        for key in keys:
            self.hits.append((line, key, self._produced(values)))

    def call(self, node: Node) -> None:
        func = node.child_by_field_name("function")
        if func is None or func.type != "member_expression":
            return
        prop = text_of(func.child_by_field_name("property"))
        target = _unwrap(func.child_by_field_name("object"))
        if target is None:
            return
        if prop in {"includes", "has", "indexOf"} and target.type == "array":
            for lit in _candidates(target):
                if lit in self.tests:
                    self.hits.append((line_of(node), lit, self._produced(self._guarded(node))))
        elif prop == "get" and target.type == "new_expression":
            args = call_args(target)
            if not args or args[0].type != "array":
                return
            pairs = [p for p in args[0].named_children if p.type == "array" and p.named_children]
            values: set[Literal] = set()
            for p in pairs:
                for v in p.named_children[1:]:
                    values |= literals_in(v)
            for p in pairs:
                key = literal_of(p.named_children[0])
                if key is not None and not is_trivial(key) and key in self.tests:
                    self.hits.append((line_of(node), key, self._produced(values)))


def overfit_findings_ts(
    path: str, head_text: str, added_lines: set[int], test_literals: frozenset[Literal]
) -> list[Finding]:
    tree = parse(path, head_text)
    if tree is None or not added_lines:
        return []
    scanner = _Scanner(added_lines, test_literals)
    for node in walk(tree.root_node):
        if line_of(node) not in added_lines:
            continue
        if node.type == "binary_expression":
            scanner.comparison(node)
        elif node.type == "switch_case":
            scanner.switch_case(node)
        elif node.type == "subscript_expression":
            obj = _unwrap(node.child_by_field_name("object"))
            if obj is not None and obj.type == "object":
                scanner.table(obj, line_of(node))
        elif node.type == "call_expression":
            scanner.call(node)
    return findings_from_hits(path, head_text, scanner.hits)
