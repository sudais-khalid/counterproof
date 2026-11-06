"""RuleForge for Python (FR-MU-02) with the static equivalence filter (FR-MU-07).

Every candidate replaces exactly one syntax node inside the mutation scope. The result is
expressed as a line-range replacement (the same shape LLM mutants use), so applying, storing
and explaining a mutant does not depend on how it was produced.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, ParentNodeProvider, PositionProvider

from counterproof.probes.pyast import parse

# Prior plausibility per operator (1-5), used for selection when no LLM scores exist.
PRIORS: Final[dict[str, int]] = {
    "boundary": 5,
    "relational": 4,
    "constant": 4,
    "arithmetic": 3,
    "negation": 3,
    "return_value": 3,
    "exception_swallow": 3,
    "statement_deletion": 2,
    "argument_swap": 2,
}

_BOUNDARY: Final[dict[type[cst.BaseCompOp], Callable[[], cst.BaseCompOp]]] = {
    cst.LessThan: cst.LessThanEqual,
    cst.LessThanEqual: cst.LessThan,
    cst.GreaterThan: cst.GreaterThanEqual,
    cst.GreaterThanEqual: cst.GreaterThan,
}
_RELATIONAL: Final[dict[type[cst.BaseCompOp], Callable[[], cst.BaseCompOp]]] = {
    cst.Equal: cst.NotEqual,
    cst.NotEqual: cst.Equal,
    cst.LessThan: cst.GreaterThan,
    cst.GreaterThan: cst.LessThan,
    cst.LessThanEqual: cst.GreaterThanEqual,
    cst.GreaterThanEqual: cst.LessThanEqual,
    cst.In: cst.NotIn,
    cst.NotIn: cst.In,
    cst.Is: cst.IsNot,
    cst.IsNot: cst.Is,
}
_ARITHMETIC: Final[dict[type[cst.BaseBinaryOp], Callable[[], cst.BaseBinaryOp]]] = {
    cst.Add: cst.Subtract,
    cst.Subtract: cst.Add,
    cst.Multiply: cst.Divide,
    cst.Divide: cst.Multiply,
    cst.FloorDivide: cst.Divide,
    cst.Modulo: cst.FloorDivide,
}
_AUG_ARITHMETIC: Final[dict[type[cst.BaseAugOp], Callable[[], cst.BaseAugOp]]] = {
    cst.AddAssign: cst.SubtractAssign,
    cst.SubtractAssign: cst.AddAssign,
    cst.MultiplyAssign: cst.DivideAssign,
    cst.DivideAssign: cst.MultiplyAssign,
}
_TERMINATORS: Final = (cst.Return, cst.Raise, cst.Continue, cst.Break)


@dataclass(frozen=True)
class Candidate:
    operator: str
    file: str
    line: int
    start_line: int
    end_line: int
    original: str
    replacement: str
    equivalent_reason: str | None = None
    source: str = "rule"
    plausibility: int | None = None
    """LLM-assigned plausibility (1-5); rule mutants use the operator prior instead."""
    rationale: str = ""

    @property
    def prior(self) -> int:
        if self.plausibility is not None:
            return self.plausibility
        return PRIORS.get(self.operator, 3)


# -- constant folding (FR-MU-07 stage 1) ------------------------------------------------

_FOLD_OPS: Final[dict[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


def _fold(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float | complex):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_fold(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _FOLD_OPS:
        return _FOLD_OPS[type(node.op)](_fold(node.left), _fold(node.right))
    raise ValueError("not foldable")


def folds_to_same_value(original: str, mutated: str) -> bool:
    """Both expressions are literal arithmetic and evaluate to the same value."""
    try:
        a = _fold(ast.parse(original.strip(), mode="eval").body)
        b = _fold(ast.parse(mutated.strip(), mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return False
    return bool(a == b and type(a) is type(b))


# -- operators ---------------------------------------------------------------------------


def _numeric_string_plus_one(value: str) -> str | None:
    """``"0.05"`` -> ``"0.06"``: perturb a numeric string in its last decimal place."""
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int):
        return None
    step = Decimal(1).scaleb(min(exponent, 0))
    return str(number + step)


def _parenthesise(expr: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(expr, cst.Name | cst.Attribute | cst.Call | cst.Subscript) or expr.lpar:
        return expr
    return expr.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])


def _is_docstring(node: cst.SimpleString, parents: Mapping[cst.CSTNode, cst.CSTNode]) -> bool:
    parent = parents.get(node)
    return isinstance(parent, cst.Expr)


Mutation = tuple[str, cst.CSTNode, cst.CSTNode]
Parents = Mapping[cst.CSTNode, cst.CSTNode]


def _comparison(node: cst.Comparison) -> Iterator[Mutation]:
    for i, target in enumerate(node.comparisons):
        op_type = type(target.operator)
        for name, table in (("boundary", _BOUNDARY), ("relational", _RELATIONAL)):
            if op_type in table:
                comparisons = [*node.comparisons]
                comparisons[i] = target.with_changes(operator=table[op_type]())
                yield name, node, node.with_changes(comparisons=comparisons)


def _condition(node: cst.If | cst.While | cst.IfExp) -> Iterator[Mutation]:
    test = node.test
    if isinstance(test, cst.UnaryOperation) and isinstance(test.operator, cst.Not):
        negated: cst.BaseExpression = test.expression
    else:
        negated = cst.UnaryOperation(operator=cst.Not(), expression=_parenthesise(test))
    yield "negation", node, node.with_changes(test=negated)


def _number(node: cst.Integer | cst.Float) -> Iterator[Mutation]:
    raw = node.value.replace("_", "")
    try:
        if isinstance(node, cst.Integer):
            yield "constant", node, cst.Integer(str(int(raw, 0) + 1))
        else:
            yield "constant", node, cst.Float(repr(float(raw) + 1.0))
    except ValueError:
        return


def _string(node: cst.SimpleString, parents: Parents) -> Iterator[Mutation]:
    raw = node.evaluated_value
    if _is_docstring(node, parents) or not isinstance(raw, str) or not raw:
        return
    quote = node.quote
    bumped = _numeric_string_plus_one(raw)
    yield (
        "constant",
        node,
        cst.SimpleString(f"{quote}{bumped if bumped is not None else ''}{quote}"),
    )


def _statement(node: cst.SimpleStatementLine) -> Iterator[Mutation]:
    if len(node.body) != 1:
        return
    stmt = node.body[0]
    if isinstance(stmt, cst.Raise):
        yield "exception_swallow", node, node.with_changes(body=[cst.Pass()])
    elif isinstance(stmt, cst.Assign | cst.AugAssign | cst.AnnAssign) or (
        isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.Call)
    ):
        yield "statement_deletion", node, node.with_changes(body=[cst.Pass()])


def _call(node: cst.Call) -> Iterator[Mutation]:
    positional = [a for a in node.args if a.keyword is None and not a.star]
    if len(positional) < 2:
        return
    a, b = positional[0].value, positional[1].value
    same_kind = (isinstance(a, cst.Name) and isinstance(b, cst.Name)) or (
        type(a) is type(b) and isinstance(a, cst.Integer | cst.Float | cst.SimpleString)
    )
    if same_kind and not a.deep_equals(b):
        args = [*node.args]
        i, j = args.index(positional[0]), args.index(positional[1])
        args[i] = positional[0].with_changes(value=b)
        args[j] = positional[1].with_changes(value=a)
        yield "argument_swap", node, node.with_changes(args=args)


def _replacements(node: cst.CSTNode, parents: Parents) -> Iterator[Mutation]:
    """Yield ``(operator, old_node, new_node)`` for one node (FR-MU-02)."""
    if isinstance(node, cst.Comparison):
        yield from _comparison(node)
    elif isinstance(node, cst.BinaryOperation) and type(node.operator) in _ARITHMETIC:
        yield "arithmetic", node, node.with_changes(operator=_ARITHMETIC[type(node.operator)]())
    elif isinstance(node, cst.AugAssign) and type(node.operator) in _AUG_ARITHMETIC:
        new_op = _AUG_ARITHMETIC[type(node.operator)]()
        yield "arithmetic", node, node.with_changes(operator=new_op)
    elif isinstance(node, cst.BooleanOperation):
        flipped: cst.BaseBooleanOp = cst.Or() if isinstance(node.operator, cst.And) else cst.And()
        yield "negation", node, node.with_changes(operator=flipped)
    elif isinstance(node, cst.If | cst.While | cst.IfExp):
        yield from _condition(node)
    elif isinstance(node, cst.Integer | cst.Float):
        yield from _number(node)
    elif isinstance(node, cst.SimpleString):
        yield from _string(node, parents)
    elif isinstance(node, cst.Name) and node.value in {"True", "False"}:
        yield "constant", node, cst.Name("False" if node.value == "True" else "True")
    elif isinstance(node, cst.Return) and node.value is not None:
        if not (isinstance(node.value, cst.Name) and node.value.value == "None"):
            yield "return_value", node, node.with_changes(value=cst.Name("None"))
    elif isinstance(node, cst.SimpleStatementLine):
        yield from _statement(node)
    elif isinstance(node, cst.Call):
        yield from _call(node)


def _statement_of(
    node: cst.CSTNode, parents: Mapping[cst.CSTNode, cst.CSTNode]
) -> cst.CSTNode | None:
    current: cst.CSTNode | None = node
    while current is not None and not isinstance(current, cst.BaseStatement):
        current = parents.get(current)
    return current


def _unreachable(node: cst.CSTNode, parents: Mapping[cst.CSTNode, cst.CSTNode]) -> bool:
    """FR-MU-07: the statement follows a return/raise/continue/break in the same block."""
    stmt = _statement_of(node, parents)
    block = parents.get(stmt) if stmt is not None else None
    if stmt is None or not isinstance(block, cst.IndentedBlock | cst.Module):
        return False
    for sibling in block.body:
        if sibling is stmt:
            return False
        if isinstance(sibling, cst.SimpleStatementLine) and any(
            isinstance(s, _TERMINATORS) for s in sibling.body
        ):
            return True
    return False


def _line_diff(original: list[str], mutated: list[str]) -> tuple[int, int, str, str] | None:
    """``(start_line, end_line, original_text, replacement_text)`` of the changed lines."""
    if original == mutated:
        return None
    top = 0
    while top < min(len(original), len(mutated)) and original[top] == mutated[top]:
        top += 1
    bottom = 0
    while (
        bottom < min(len(original), len(mutated)) - top
        and original[-1 - bottom] == mutated[-1 - bottom]
    ):
        bottom += 1
    old = original[top : len(original) - bottom]
    new = mutated[top : len(mutated) - bottom]
    if not old:  # pure insertion: anchor on the next line so the range is never empty
        old = original[top : top + 1]
        new = [*new, *old]
    return top + 1, top + len(old), "".join(old), "".join(new)


class _Collector(cst.CSTVisitor):
    def __init__(self, positions: Mapping[cst.CSTNode, CodeRange], scope: set[int]) -> None:
        self.positions = positions
        self.scope = scope
        self.nodes: list[cst.CSTNode] = []

    def on_visit(self, node: cst.CSTNode) -> bool:
        pos = self.positions.get(node)
        if pos is not None and pos.start.line in self.scope:
            self.nodes.append(node)
        return True


def generate(path: str, text: str, scope: set[int]) -> list[Candidate]:
    """All rule mutants of ``text`` whose mutated node starts on a line in ``scope``."""
    module = parse(text)
    if module is None or not scope:
        return []
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    parents = wrapper.resolve(ParentNodeProvider)
    tree = wrapper.module
    collector = _Collector(positions, scope)
    tree.visit(collector)

    original_lines = text.splitlines(keepends=True)
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for node in collector.nodes:
        for op_name, old, new in _replacements(node, parents):
            mutated = tree.deep_replace(old, new)
            if not isinstance(mutated, cst.Module):
                continue
            mutated_text = mutated.code
            if mutated_text in seen:
                continue
            seen.add(mutated_text)
            diff = _line_diff(original_lines, mutated_text.splitlines(keepends=True))
            if diff is None:
                continue
            start, end, before, after = diff
            reason = None
            if _unreachable(old, parents):
                reason = "unreachable: follows a return/raise in the same block"
            elif isinstance(old, cst.BaseExpression) and folds_to_same_value(
                tree.code_for_node(old), cst.Module(body=[]).code_for_node(new)
            ):
                reason = "constant-folds to the same value"
            candidates.append(
                Candidate(
                    operator=op_name,
                    file=path,
                    line=positions[old].start.line,
                    start_line=start,
                    end_line=end,
                    original=before,
                    replacement=after,
                    equivalent_reason=reason,
                )
            )
    return candidates
