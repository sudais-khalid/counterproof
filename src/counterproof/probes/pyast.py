"""Python syntax analysis shared by the integrity and red-green probes (libcst).

Everything here is static: files are parsed, never imported or executed. Every function is
total: a file that does not parse yields an empty result rather than an exception, because
code under test may be syntactically broken on purpose.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

import libcst as cst
import libcst.matchers as m
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

Literal = tuple[str, str]
"""A canonical literal: ``("num", "2.665")``, ``("str", "abc")``, ``("seq", "(1,1,1)")``."""

TRIVIAL: Final[frozenset[Literal]] = frozenset(
    {
        ("num", "0"),
        ("num", "1"),
        ("num", "-1"),
        ("const", "True"),
        ("const", "False"),
        ("const", "None"),
        ("str", ""),
        ("seq", "()"),
    }
)
SKIP_MARKERS: Final = frozenset({"skip", "skipif", "xfail"})
SKIP_CALLS: Final = frozenset({"pytest.skip", "pytest.xfail", "self.skipTest", "unittest.skip"})
_NEUTRAL_HANDLER_TYPES: Final = frozenset({"AssertionError", "Exception", "BaseException"})


def parse(text: str) -> cst.Module | None:
    try:
        return cst.parse_module(text)
    except (cst.ParserSyntaxError, RecursionError):
        return None


def dotted(node: cst.BaseExpression) -> str:
    """``pytest.mark.skip`` for an attribute chain, ``""`` for anything else."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        head = dotted(node.value)
        return f"{head}.{node.attr.value}" if head else ""
    if isinstance(node, cst.Call):
        return dotted(node.func)
    return ""


# -- literals ---------------------------------------------------------------------------


def _number(text: str) -> str | None:
    try:
        value = Decimal(text.replace("_", ""))
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    normalised = value.normalize()
    return "0" if normalised.is_zero() else format(normalised, "f")


def literal_of(node: cst.BaseExpression) -> Literal | None:
    """Canonical literal value of ``node``, looking through literal-only constructor calls
    such as ``Decimal("2.665")`` or ``Money.of("100.00")``. ``None`` if not a literal."""
    if isinstance(node, cst.Integer | cst.Float):
        num = _number(node.value)
        return ("num", num) if num is not None else None
    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Minus):
        inner = literal_of(node.expression)
        if inner and inner[0] == "num":
            return ("num", "0" if inner[1] == "0" else f"-{inner[1]}")
        return None
    if isinstance(node, cst.SimpleString):
        value = node.evaluated_value
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        num = _number(value) if value.strip() else None
        return ("num", num) if num is not None else ("str", value)
    if isinstance(node, cst.Name) and node.value in {"True", "False", "None"}:
        return ("const", node.value)
    if isinstance(node, cst.List | cst.Tuple | cst.Set):
        parts = [literal_of(e.value) for e in node.elements if isinstance(e, cst.Element)]
        if len(parts) != len(node.elements) or any(p is None for p in parts):
            return None
        return ("seq", "(" + ",".join(f"{p[0]}:{p[1]}" for p in parts if p) + ")")
    if isinstance(node, cst.Call) and node.args:
        values = [literal_of(a.value) for a in node.args]
        if all(v is not None for v in values) and len(values) == 1:
            return values[0]
    return None


def is_trivial(lit: Literal) -> bool:
    return lit in TRIVIAL


class _LiteralCollector(cst.CSTVisitor):
    def __init__(self) -> None:
        self.found: set[Literal] = set()

    def on_visit(self, node: cst.CSTNode) -> bool:
        if isinstance(node, cst.BaseExpression):
            lit = literal_of(node)
            if lit is not None:
                self.found.add(lit)
        return True


def literals_in(node: cst.CSTNode) -> set[Literal]:
    """Every literal appearing anywhere under ``node`` (test inputs and expected values)."""
    collector = _LiteralCollector()
    node.visit(collector)
    return collector.found


# -- test functions -----------------------------------------------------------------------


@dataclass
class TestFunction:
    __test__ = False  # not a pytest class

    nodeid_suffix: str
    """``test_x`` or ``TestK::test_x``."""
    line: int
    body_hash: str
    code: str
    asserts: int
    eq_asserts: int
    truthy_or_in_asserts: int
    skips: set[str] = field(default_factory=set)
    neutralised: list[tuple[int, str]] = field(default_factory=list)
    approx_tolerances: list[Decimal] = field(default_factory=list)
    raises_without_match: int = 0
    patch_targets: list[tuple[int, str]] = field(default_factory=list)


def _marker_name(decorator: cst.Decorator) -> str | None:
    """``skip`` for ``@pytest.mark.skip(...)``/``@mark.skip``/``@unittest.skip``."""
    name = dotted(decorator.decorator)
    last = name.rsplit(".", 1)[-1]
    if last in SKIP_MARKERS and ("mark" in name.split(".") or name.startswith("unittest")):
        return last
    if name.startswith("unittest.skip") or name in {"unittest.expectedFailure"}:
        return name.rsplit(".", 1)[-1]
    return None


def _strip_comments(code: str) -> str:
    return "\n".join(line.split("#", 1)[0].rstrip() for line in code.splitlines() if line.strip())


def _is_truthy_constant(node: cst.BaseExpression) -> bool:
    """A bare literal that is always true. Calls are never constants here: ``f(2.5)`` is a
    real check even though ``literal_of`` looks through literal-only constructor calls."""
    if isinstance(node, cst.Call):
        return False
    lit = literal_of(node)
    if lit is None:
        return False
    kind, value = lit
    if kind == "const":
        return value == "True"
    if kind == "num":
        return value != "0"
    return bool(value) and value != "()"


Positions = Mapping[cst.CSTNode, CodeRange]


class _FunctionScanner(cst.CSTVisitor):
    def __init__(self, fn: TestFunction, positions: Positions) -> None:
        self.fn = fn
        self.positions = positions
        self._wrapped: set[int] = set()

    def _line(self, node: cst.CSTNode) -> int:
        return self.positions[node].start.line

    def visit_Try(self, node: cst.Try) -> None:
        # FR-IN-05: assertions in the body of a try whose handler swallows the failure.
        if any(self._handler_swallows(h) for h in node.handlers):
            for found in m.findall(node.body, m.Assert() | m.Call()):
                self._wrapped.add(id(found))

    @staticmethod
    def _handler_swallows(handler: cst.ExceptHandler) -> bool:
        if handler.type is not None:
            names = (
                [dotted(e.value) for e in handler.type.elements]
                if isinstance(handler.type, cst.Tuple)
                else [dotted(handler.type)]
            )
            if not set(names) & _NEUTRAL_HANDLER_TYPES:
                return False
        return not m.findall(handler, m.Raise())

    def visit_Assert(self, node: cst.Assert) -> None:
        self.fn.asserts += 1
        test = node.test
        line = self._line(node)
        if isinstance(test, cst.Comparison) and any(
            isinstance(c.operator, cst.Equal) for c in test.comparisons
        ):
            self.fn.eq_asserts += 1
        elif not isinstance(test, cst.Comparison) or any(
            isinstance(c.operator, cst.In) for c in test.comparisons
        ):
            self.fn.truthy_or_in_asserts += 1
        # FR-IN-05: tautologies and `or True`.
        if _is_truthy_constant(test):
            self.fn.neutralised.append((line, "assert of a constant"))
        elif isinstance(test, cst.BooleanOperation) and isinstance(test.operator, cst.Or):
            operands = _or_operands(test)
            if any(_is_truthy_constant(o) for o in operands):
                self.fn.neutralised.append(
                    (line, "`or <constant>` makes the assertion always pass")
                )
        if id(node) in self._wrapped:
            self.fn.neutralised.append((line, "assertion inside try/except that swallows failures"))

    def visit_Call(self, node: cst.Call) -> None:
        name = dotted(node.func)
        line = self._line(node)
        last = name.rsplit(".", 1)[-1]
        if name in SKIP_CALLS or (last in {"skip", "xfail"} and name.startswith("pytest")):
            self.fn.skips.add(f"call:{last}")
        if name.startswith("self.assert") or name in {"self.fail", "pytest.fail"}:
            self.fn.asserts += 1
            if last in {"assertTrue"} and node.args and _is_truthy_constant(node.args[0].value):
                self.fn.neutralised.append((line, "assertTrue of a constant"))
            if id(node) in self._wrapped:
                self.fn.neutralised.append(
                    (line, "assertion inside try/except that swallows failures")
                )
        if name in {"pytest.raises", "raises", "pytest.warns"}:
            self.fn.asserts += 1
            if name.endswith("raises") and not any(
                a.keyword and a.keyword.value == "match" for a in node.args
            ):
                self.fn.raises_without_match += 1
        if last == "approx":
            for arg in node.args:
                if arg.keyword and arg.keyword.value in {"rel", "abs"}:
                    lit = literal_of(arg.value)
                    if lit and lit[0] == "num":
                        self.fn.approx_tolerances.append(Decimal(lit[1]))
        # FR-IN-06 patch targets.
        if last in {"patch", "setattr"} or name.endswith("patch.object"):
            target = _patch_target(name, node)
            if target:
                self.fn.patch_targets.append((line, target))


def _or_operands(node: cst.BaseExpression) -> list[cst.BaseExpression]:
    if isinstance(node, cst.BooleanOperation) and isinstance(node.operator, cst.Or):
        return _or_operands(node.left) + _or_operands(node.right)
    return [node]


def _patch_target(name: str, call: cst.Call) -> str | None:
    """``ledger.discounts.percent_discount`` or ``<expr>.percent_discount``."""
    args = [a for a in call.args if a.keyword is None]
    if not args:
        return None
    first = literal_of(args[0].value)
    if name.endswith(("patch.object", "setattr")) and len(args) >= 2:
        attr = literal_of(args[1].value)
        if first is None and attr and attr[0] == "str":
            return f"{dotted(args[0].value) or '?'}.{attr[1]}"
    if first and first[0] == "str" and "." in first[1]:
        return first[1]
    return None


class _TestCollector(cst.CSTVisitor):
    def __init__(self, module: cst.Module, positions: Positions) -> None:
        self.module = module
        self.positions = positions
        self.tests: dict[str, TestFunction] = {}
        self._class_stack: list[tuple[str, set[str]]] = []
        self.module_skips: set[str] = set()

    def visit_Assign(self, node: cst.Assign) -> None:
        if self._class_stack:
            return
        for target in node.targets:
            if m.matches(target.target, m.Name("pytestmark")):
                for call_name in _names_in(node.value):
                    last = call_name.rsplit(".", 1)[-1]
                    if last in SKIP_MARKERS and "mark" in call_name:
                        self.module_skips.add(f"module:{last}")

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if not node.name.value.startswith("Test"):
            return False
        skips = {f"class:{n}" for d in node.decorators if (n := _marker_name(d))}
        outer = self._class_stack[-1][1] if self._class_stack else set()
        self._class_stack.append((node.name.value, skips | outer))
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        if self._class_stack and self._class_stack[-1][0] == original_node.name.value:
            self._class_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        name = node.name.value
        if not name.startswith("test"):
            return False
        prefix = "::".join(c for c, _ in self._class_stack)
        suffix = f"{prefix}::{name}" if prefix else name
        body_code = self.module.code_for_node(node.body)
        fn = TestFunction(
            nodeid_suffix=suffix,
            line=self.positions[node].start.line,
            body_hash=hashlib.sha256(_strip_comments(body_code).encode()).hexdigest(),
            code=self.module.code_for_node(node),
            asserts=0,
            eq_asserts=0,
            truthy_or_in_asserts=0,
        )
        fn.skips |= {f"decorator:{n}" for d in node.decorators if (n := _marker_name(d))}
        if self._class_stack:
            fn.skips |= self._class_stack[-1][1]
        node.body.visit(_FunctionScanner(fn, self.positions))
        self.tests[suffix] = fn
        return False


def _names_in(node: cst.CSTNode) -> Iterator[str]:
    for found in m.findall(node, m.Attribute() | m.Name()):
        if isinstance(found, cst.Attribute | cst.Name):
            name = dotted(found)
            if name:
                yield name


@dataclass(frozen=True)
class TestFile:
    __test__ = False

    tests: dict[str, TestFunction]
    module_skips: frozenset[str]
    literals: frozenset[Literal]


def analyse_test_file(text: str) -> TestFile | None:
    module = parse(text)
    if module is None:
        return None
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    collector = _TestCollector(wrapper.module, positions)
    wrapper.module.visit(collector)
    for fn in collector.tests.values():
        fn.skips |= collector.module_skips
    return TestFile(
        tests=collector.tests,
        module_skips=frozenset(collector.module_skips),
        literals=frozenset(literals_in(wrapper.module)),
    )


# -- source symbols ----------------------------------------------------------------------


def defined_symbols(text: str) -> set[str]:
    """Top-level and class-level names, function parameters, and ``Class.method`` names."""
    module = parse(text)
    if module is None:
        return set()
    names: set[str] = set()
    for node in m.findall(module, m.FunctionDef() | m.ClassDef()):
        if isinstance(node, cst.FunctionDef):
            names.add(node.name.value)
            params = node.params
            for p in [*params.posonly_params, *params.params, *params.kwonly_params]:
                names.add(f"{node.name.value}({p.name.value})")
        elif isinstance(node, cst.ClassDef):
            names.add(node.name.value)
    for assign in m.findall(module, m.Assign() | m.AnnAssign()):
        targets = (
            [t.target for t in assign.targets]
            if isinstance(assign, cst.Assign)
            else [assign.target]
            if isinstance(assign, cst.AnnAssign)
            else []
        )
        names |= {t.value for t in targets if isinstance(t, cst.Name)}
    return names


def added_symbols(base_text: str | None, head_text: str) -> set[str]:
    """Symbols (and ``func(param)`` pairs) present in head but not in base."""
    return defined_symbols(head_text) - defined_symbols(base_text or "")


def top_level_definitions(text: str) -> set[str]:
    module = parse(text)
    if module is None:
        return set()
    names: set[str] = set()
    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef | cst.ClassDef):
            names.add(stmt.name.value)
        elif isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Assign):
                    names |= {
                        t.target.value for t in small.targets if isinstance(t.target, cst.Name)
                    }
    return names


def module_name(path: str, roots: Sequence[str] = ("src/",)) -> str:
    """``src/ledger/discounts.py`` -> ``ledger.discounts``."""
    for root in roots:
        if path.startswith(root):
            path = path[len(root) :]
            break
    path = re.sub(r"\.pyi?$", "", path)
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
