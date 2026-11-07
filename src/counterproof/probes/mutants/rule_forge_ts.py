"""RuleForge for TypeScript and JavaScript (FR-MU-02), mutation scope (FR-MU-01) and the
static equivalence filter (FR-MU-07), over tree-sitter.

Each candidate replaces one node's bytes (an operator token, a literal, a statement). The
result is the same line-range :class:`Candidate` the Python forge produces, so applying,
selecting, storing and explaining mutants is language-independent. Type positions
(annotations, aliases, interfaces) and imports are never mutated: they have no runtime effect.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation
from typing import Final

from tree_sitter import Node, Tree

from counterproof.probes.mutants.rule_forge_py import Candidate, _line_diff, folds_to_same_value
from counterproof.probes.tsast import (
    FUNCTION_NODES,
    call_args,
    line_of,
    parse,
    string_value,
    text_of,
    walk,
)

MAX_CONTEXT_LINES: Final = 2
_BOUNDARY: Final = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_RELATIONAL: Final = {
    "===": "!==",
    "!==": "===",
    "==": "!=",
    "!=": "==",
    "<": ">",
    ">": "<",
    "<=": ">=",
    ">=": "<=",
}
_ARITHMETIC: Final = {"+": "-", "-": "+", "*": "/", "/": "*", "%": "/", "**": "*"}
_LOGICAL: Final = {"&&": "||", "||": "&&"}
_AUGMENTED: Final = {"+=": "-=", "-=": "+=", "*=": "/=", "/=": "*="}
_TYPE_CONTEXT: Final = re.compile(r"type|interface|import|export_clause|decorator")
_TERMINATORS: Final = frozenset(
    {"return_statement", "throw_statement", "continue_statement", "break_statement"}
)
_NUMERIC_EXPR: Final = re.compile(r"^[\d.\s+\-*/%()_]+$")

Edit = tuple[str, int, int, str]
"""``(operator, start_byte, end_byte, replacement)``."""


def _in_type_or_import(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if _TYPE_CONTEXT.search(current.type) and current.type != "export_statement":
            return True
        current = current.parent
    return False


def _operator_token(node: Node) -> Node | None:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op
    # augmented_assignment_expression has no operator field in some grammar versions
    for child in node.children:
        if not child.is_named and text_of(child) in _AUGMENTED:
            return child
    return None


def _bump(raw: str) -> str | None:
    """``5000`` -> ``5001``; ``0.05`` -> ``0.06`` (last decimal place)."""
    clean = raw.replace("_", "")
    try:
        if re.fullmatch(r"\d+", clean):
            return str(int(clean) + 1)
        number = Decimal(clean)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int):
        return None
    return str(number + Decimal(1).scaleb(min(exponent, 0)))


def _binary(node: Node) -> Iterator[Edit]:
    op = _operator_token(node)
    if op is None:
        return
    token = text_of(op)
    for name, table in (("boundary", _BOUNDARY), ("relational", _RELATIONAL)):
        if token in table:
            yield name, op.start_byte, op.end_byte, table[token]
    if token in _ARITHMETIC:
        yield "arithmetic", op.start_byte, op.end_byte, _ARITHMETIC[token]
    if token in _LOGICAL:
        yield "negation", op.start_byte, op.end_byte, _LOGICAL[token]


def _augmented(node: Node) -> Iterator[Edit]:
    op = _operator_token(node)
    if op is not None and text_of(op) in _AUGMENTED:
        yield "arithmetic", op.start_byte, op.end_byte, _AUGMENTED[text_of(op)]


def _condition(node: Node) -> Iterator[Edit]:
    cond = node.child_by_field_name("condition")
    if cond is None:
        return
    inner = cond.named_children[0] if cond.type == "parenthesized_expression" else cond
    if inner is None:
        return
    negated = inner.type == "unary_expression"
    if negated and text_of(inner.child_by_field_name("operator")) == "!":
        arg = inner.child_by_field_name("argument")
        if arg is not None:
            yield "negation", inner.start_byte, inner.end_byte, text_of(arg)
    else:
        yield "negation", inner.start_byte, inner.end_byte, f"!({text_of(inner)})"


def _number(node: Node) -> Iterator[Edit]:
    bumped = _bump(text_of(node))
    if bumped is not None:
        yield "constant", node.start_byte, node.end_byte, bumped


def _string(node: Node) -> Iterator[Edit]:
    value = string_value(node)
    parent = node.parent
    if not value or (parent is not None and parent.type in {"pair", "import_statement"}):
        return
    quote = text_of(node)[0]
    bumped = _bump(value)
    replacement = bumped if bumped is not None and value.strip() == value else ""
    yield "constant", node.start_byte, node.end_byte, f"{quote}{replacement}{quote}"


def _boolean(node: Node) -> Iterator[Edit]:
    flipped = "false" if node.type == "true" else "true"
    yield "constant", node.start_byte, node.end_byte, flipped


def _return(node: Node) -> Iterator[Edit]:
    returned = node.named_children[0] if node.named_children else None
    if returned is not None and returned.type != "undefined":
        yield "return_value", returned.start_byte, returned.end_byte, "undefined"


def _throw(node: Node) -> Iterator[Edit]:
    yield "exception_swallow", node.start_byte, node.end_byte, ";"


def _statement(node: Node) -> Iterator[Edit]:
    expr = node.named_children[0] if node.named_children else None
    if expr is not None and expr.type in _DELETABLE:
        yield "statement_deletion", node.start_byte, node.end_byte, ";"


def _call(node: Node) -> Iterator[Edit]:
    args = call_args(node)
    if len(args) < 2:
        return
    a, b = args[0], args[1]
    same_kind = (a.type == b.type == "identifier") or (
        a.type == b.type and a.type in {"number", "string"}
    )
    if same_kind and text_of(a) != text_of(b):
        raw = node.text or b""
        base = node.start_byte
        between = raw[a.end_byte - base : b.start_byte - base].decode("utf-8", "replace")
        yield "argument_swap", a.start_byte, b.end_byte, f"{text_of(b)}{between}{text_of(a)}"


_DELETABLE: Final = frozenset(
    {
        "assignment_expression",
        "augmented_assignment_expression",
        "call_expression",
        "update_expression",
    }
)
_OPERATORS: Final[dict[str, Callable[[Node], Iterator[Edit]]]] = {
    "binary_expression": _binary,
    "augmented_assignment_expression": _augmented,
    "if_statement": _condition,
    "while_statement": _condition,
    "ternary_expression": _condition,
    "number": _number,
    "string": _string,
    "true": _boolean,
    "false": _boolean,
    "return_statement": _return,
    "throw_statement": _throw,
    "expression_statement": _statement,
    "call_expression": _call,
}


def _edits(node: Node) -> Iterator[Edit]:
    """Yield ``(operator, start_byte, end_byte, replacement)`` for one node (FR-MU-02)."""
    handler = _OPERATORS.get(node.type)
    if handler is not None:
        yield from handler(node)


def _unreachable(node: Node) -> bool:
    """FR-MU-07: the statement follows a return/throw/continue/break in the same block."""
    stmt: Node | None = node
    while stmt is not None and not stmt.type.endswith("statement"):
        stmt = stmt.parent
    if stmt is None or stmt.parent is None or stmt.parent.type != "statement_block":
        return False
    for sibling in stmt.parent.named_children:
        if sibling.id == stmt.id:
            return False
        if sibling.type in _TERMINATORS:
            return True
    return False


def generate_ts(path: str, text: str, scope: set[int]) -> list[Candidate]:
    """All rule mutants of ``text`` whose mutated node starts on a line in ``scope``."""
    tree = parse(path, text)
    if tree is None or not scope:
        return []
    raw = text.encode("utf-8")
    original_lines = text.splitlines(keepends=True)
    seen: set[bytes] = set()
    candidates: list[Candidate] = []
    for node in walk(tree.root_node):
        if line_of(node) not in scope or _in_type_or_import(node):
            continue
        for op_name, start, end, replacement in _edits(node):
            mutated = raw[:start] + replacement.encode("utf-8") + raw[end:]
            if mutated in seen or mutated == raw:
                continue
            seen.add(mutated)
            mutated_text = mutated.decode("utf-8", "replace")
            if parse(path, mutated_text) is None:
                continue
            diff = _line_diff(original_lines, mutated_text.splitlines(keepends=True))
            if diff is None:
                continue
            first, last, before, after = diff
            old_text = raw[start:end].decode("utf-8", "replace")
            reason = None
            if _unreachable(node):
                reason = "unreachable: follows a return/throw in the same block"
            elif (
                node.type == "binary_expression"
                and _NUMERIC_EXPR.match(text_of(node))
                and folds_to_same_value(
                    text_of(node),
                    text_of(node).replace(old_text, replacement, 1),
                )
            ):
                reason = "constant-folds to the same value"
            candidates.append(
                Candidate(
                    operator=op_name,
                    file=path,
                    line=line_of(node),
                    start_line=first,
                    end_line=last,
                    original=before,
                    replacement=after,
                    equivalent_reason=reason,
                )
            )
    return candidates


# -- FR-MU-01 scope ----------------------------------------------------------------------


def _blocks(lines: set[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for n in sorted(lines):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def _structure(tree: Tree) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]], list[int]]:
    conditions: list[tuple[int, int, int]] = []
    functions: list[tuple[int, int]] = []
    returns: list[int] = []
    for node in walk(tree.root_node):
        if node.type in {"if_statement", "while_statement"}:
            cond = node.child_by_field_name("condition")
            body = node.child_by_field_name("consequence") or node.child_by_field_name("body")
            if cond is not None and body is not None:
                conditions.append((line_of(cond), line_of(body), body.end_point.row + 1))
        elif node.type in FUNCTION_NODES:
            functions.append((line_of(node), node.end_point.row + 1))
        elif node.type == "return_statement":
            returns.append(line_of(node))
    return conditions, functions, returns


def mutation_scope_ts(path: str, head_text: str, changed_lines: set[int]) -> set[int]:
    """Lines RuleForge may mutate in one TS/JS source file."""
    tree = parse(path, head_text)
    if tree is None or not changed_lines:
        return set(changed_lines)
    conditions, functions, returns = _structure(tree)
    scope = set(changed_lines)
    for first, last in _blocks(changed_lines):
        context: list[int] = []
        enclosing = [c for c, start, end in conditions if start <= first and last <= end]
        if enclosing and max(enclosing) not in changed_lines:
            context.append(max(enclosing))
        inner = [(s, e) for s, e in functions if s <= first and last <= e]
        if inner:
            fn_start, fn_end = max(inner)
            following = [r for r in returns if last < r <= fn_end and r > fn_start]
            if following and min(following) not in changed_lines:
                context.append(min(following))
        scope.update(context[:MAX_CONTEXT_LINES])
    return scope
