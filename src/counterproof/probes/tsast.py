"""TypeScript and JavaScript syntax analysis (tree-sitter), the twin of :mod:`pyast`.

Produces the same :class:`~counterproof.probes.pyast.TestFile` and literal shapes as the
Python side, so the integrity rules (FR-IN-01 to FR-IN-07), red-green and the mutant probe
work unchanged on Vitest and Jest suites. Everything is static and total: a file that does
not parse yields an empty result. Literals use the Python spellings (``True``, ``None``)
so that trivial values are recognised the same way in both languages.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import PurePosixPath
from typing import Final

import tree_sitter_javascript as ts_js
import tree_sitter_typescript as ts_ts
from tree_sitter import Language, Node, Parser, Tree

from counterproof.probes.pyast import Literal, TestFile, TestFunction

TS_SUFFIXES: Final = (".ts", ".mts", ".cts", ".tsx")
JS_SUFFIXES: Final = (".js", ".mjs", ".cjs", ".jsx")
SUFFIXES: Final = TS_SUFFIXES + JS_SUFFIXES
TEST_CALLS: Final = frozenset({"it", "test", "xit", "xtest", "fit"})
SUITE_CALLS: Final = frozenset({"describe", "suite", "xdescribe", "fdescribe", "context"})
SKIP_MODIFIERS: Final = frozenset({"skip", "todo", "only", "skipIf", "runIf", "fails"})
EQ_MATCHERS: Final = frozenset({"toBe", "toEqual", "toStrictEqual", "toBeCloseTo", "toMatchObject"})
LOOSE_MATCHERS: Final = frozenset(
    {
        "toBeTruthy",
        "toBeFalsy",
        "toBeDefined",
        "toBeUndefined",
        "toContain",
        "toContainEqual",
        "toMatch",
        "toBeGreaterThan",
        "toBeGreaterThanOrEqual",
        "toBeLessThan",
        "toBeLessThanOrEqual",
        "toHaveLength",
        "toBeTypeOf",
        "toBeInstanceOf",
        "toBeNull",
        "toHaveProperty",
        "toBeNaN",
    }
)
FUNCTION_NODES: Final = frozenset(
    {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "generator_function_declaration",
        "function",
    }
)
STRING_NODES: Final = frozenset({"string", "template_string"})


def is_ts_path(path: str) -> bool:
    return path.endswith(SUFFIXES) and not path.endswith(".d.ts")


@cache
def _language(kind: str) -> Language:
    if kind == "tsx":
        return Language(ts_ts.language_tsx())
    if kind == "ts":
        return Language(ts_ts.language_typescript())
    return Language(ts_js.language())


def _kind(path: str) -> str:
    if path.endswith(".tsx"):
        return "tsx"
    if path.endswith(TS_SUFFIXES):
        return "ts"
    return "js"


def parse_tree(path: str, text: str) -> Tree:
    return Parser(_language(_kind(path))).parse(text.encode("utf-8"))


def parse(path: str, text: str) -> Tree | None:
    """The syntax tree, or ``None`` when the file has a syntax error."""
    tree = parse_tree(path, text)
    return None if tree.root_node.has_error else tree


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def text_of(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def line_of(node: Node) -> int:
    return node.start_point.row + 1


def normalised(path: str, text: str) -> str | None:
    """Syntax tree without whitespace and comments (FR-MU-06), or ``None`` if invalid."""
    tree = parse(path, text)
    if tree is None:
        return None
    parts: list[str] = []
    for node in walk(tree.root_node):
        if node.type == "comment":
            continue
        parts.append(node.type if node.child_count else f"{node.type}:{text_of(node)}")
    return " ".join(parts)


# -- literals ---------------------------------------------------------------------------


def _number(raw: str) -> str | None:
    raw = raw.replace("_", "")
    try:
        value = Decimal(int(raw, 0)) if raw[:2].lower() in {"0x", "0o", "0b"} else Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    normalised_value = value.normalize()
    return "0" if normalised_value.is_zero() else format(normalised_value, "f")


def string_value(node: Node) -> str | None:
    """Value of a string literal or a template string without substitutions."""
    if node.type == "string":
        return "".join(text_of(c) for c in node.named_children if c.type == "string_fragment")
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        return text_of(node)[1:-1]
    return None


def literal_of(node: Node) -> Literal | None:
    kind = node.type
    if kind == "number":
        num = _number(text_of(node))
        return ("num", num) if num is not None else None
    if kind == "unary_expression" and text_of(node.child_by_field_name("operator")) == "-":
        inner = node.child_by_field_name("argument")
        lit = literal_of(inner) if inner is not None else None
        if lit and lit[0] == "num":
            return ("num", "0" if lit[1] == "0" else f"-{lit[1]}")
        return None
    if kind in STRING_NODES:
        value = string_value(node)
        if value is None:
            return None
        num = _number(value) if value.strip() else None
        return ("num", num) if num is not None else ("str", value)
    if kind in {"true", "false"}:
        return ("const", "True" if kind == "true" else "False")
    if kind in {"null", "undefined"}:
        return ("const", "None")
    if kind == "parenthesized_expression" and node.named_child_count == 1:
        return literal_of(node.named_children[0])
    if kind == "array":
        parts = [literal_of(c) for c in node.named_children]
        if any(p is None for p in parts):
            return None
        return ("seq", "(" + ",".join(f"{p[0]}:{p[1]}" for p in parts if p) + ")")
    if kind in {"call_expression", "new_expression"}:
        args = node.child_by_field_name("arguments")
        named = args.named_children if args is not None else []
        if len(named) == 1:
            return literal_of(named[0])
    if kind in {"as_expression", "satisfies_expression", "non_null_expression"}:
        first = node.named_children[0] if node.named_children else None
        return literal_of(first) if first is not None else None
    return None


def literals_in(node: Node) -> set[Literal]:
    found: set[Literal] = set()
    for child in walk(node):
        lit = literal_of(child)
        if lit is not None:
            found.add(lit)
    return found


# -- calls ------------------------------------------------------------------------------


def callee_chain(call: Node) -> list[str]:
    """``describe.skip.each`` -> ``["describe", "skip", "each"]`` for a call's function."""
    func = call.child_by_field_name("function")
    chain: list[str] = []
    while func is not None:
        if func.type == "identifier":
            chain.insert(0, text_of(func))
            break
        if func.type == "member_expression":
            chain.insert(0, text_of(func.child_by_field_name("property")))
            func = func.child_by_field_name("object")
        elif func.type == "call_expression":  # describe.each([...])("name", fn)
            func = func.child_by_field_name("function")
        else:
            break
    return chain


def call_args(call: Node) -> list[Node]:
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def _name_of_test(call: Node) -> str | None:
    args = call_args(call)
    if not args:
        return None
    first = args[0]
    if first.type in STRING_NODES:
        return string_value(first) if first.type == "string" else text_of(first)[1:-1]
    return None


def _callback(call: Node) -> Node | None:
    for arg in call_args(call):
        if arg.type in {"arrow_function", "function_expression", "function"}:
            return arg
    return None


def _strip_comments_ts(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return "\n".join(
        line.split("//", 1)[0].rstrip()
        for line in code.splitlines()
        if line.split("//", 1)[0].strip()
    )


def _expect_matcher(call: Node) -> tuple[Node | None, str | None, list[Node]]:
    """For ``expect(a).not.toBe(b)``: ``(a, "toBe", [b])``; ``(None, None, [])`` otherwise."""
    chain = callee_chain(call)
    if not chain or chain[0] != "expect" or len(chain) < 2:
        return None, None, []
    func = call.child_by_field_name("function")
    subject: Node | None = None
    node = func
    while node is not None and node.type == "member_expression":
        node = node.child_by_field_name("object")
    if node is not None and node.type == "call_expression":
        args = call_args(node)
        subject = args[0] if args else None
    return subject, chain[-1], call_args(call)


def _is_truthy_constant(node: Node | None) -> bool:
    if node is None or node.type in {"call_expression", "new_expression"}:
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


def _catch_swallows(try_node: Node) -> bool:
    handler = try_node.child_by_field_name("handler")
    if handler is None:
        return False
    body = handler.child_by_field_name("body")
    return body is not None and not any(n.type == "throw_statement" for n in walk(body))


def _scan(fn: TestFunction, body: Node) -> None:
    wrapped: set[int] = set()
    for node in walk(body):
        if node.type == "try_statement" and _catch_swallows(node):
            block = node.child_by_field_name("body")
            if block is not None:
                wrapped |= {n.id for n in walk(block) if n.type == "call_expression"}
    for node in walk(body):
        if node.type != "call_expression":
            continue
        chain = callee_chain(node)
        if not chain:
            continue
        line = line_of(node)
        head = chain[0]
        subject, matcher, margs = _expect_matcher(node)
        if matcher is not None:
            fn.asserts += 1
            negated = "not" in chain[1:-1]
            if matcher in EQ_MATCHERS and not negated:
                fn.eq_asserts += 1
            elif matcher in LOOSE_MATCHERS or negated:
                fn.truthy_or_in_asserts += 1
            if matcher in {"toThrow", "toThrowError"} and not margs:
                fn.raises_without_match += 1
            if matcher == "toBeCloseTo" and len(margs) >= 2:
                digits = literal_of(margs[1])
                if digits and digits[0] == "num":
                    fn.approx_tolerances.append(Decimal(10) ** -Decimal(digits[1]) / 2)
            # FR-IN-05: tautologies such as expect(true).toBe(true) or expect(1).toBeTruthy().
            if (
                subject is not None
                and literal_of(subject) is not None
                and subject.type not in {"call_expression", "new_expression"}
            ):
                expected = literal_of(margs[0]) if margs else None
                if (matcher in {"toBeTruthy", "toBeDefined"} and _is_truthy_constant(subject)) or (
                    expected is not None and expected == literal_of(subject) and not negated
                ):
                    fn.neutralised.append((line, "assertion compares a constant with itself"))
            if node.id in wrapped:
                fn.neutralised.append((line, "assertion inside try/catch that swallows failures"))
        elif head == "assert" or (head == "expect" and chain[1:] == ["assertions"]):
            fn.asserts += 1
            if (
                head == "assert"
                and len(chain) == 1
                and _is_truthy_constant(next(iter(call_args(node)), None))
            ):
                fn.neutralised.append((line, "assert of a constant"))
            if node.id in wrapped and head == "assert":
                fn.neutralised.append((line, "assertion inside try/catch that swallows failures"))
        elif head in {"vi", "jest"} and len(chain) >= 2 and chain[1] in {"mock", "spyOn", "doMock"}:
            args = call_args(node)
            if chain[1] == "spyOn" and len(args) >= 2 and args[1].type == "string":
                fn.patch_targets.append((line, f"{text_of(args[0])}.{string_value(args[1])}"))
            elif chain[1] in {"mock", "doMock"} and args and args[0].type == "string":
                fn.patch_targets.append((line, f"{string_value(args[0])}.*"))


def _test_function(call: Node, suffix: str, skips: set[str]) -> TestFunction:
    callback = _callback(call)
    body = callback.child_by_field_name("body") if callback is not None else None
    body_code = text_of(body)
    fn = TestFunction(
        nodeid_suffix=suffix,
        line=line_of(call),
        body_hash=hashlib.sha256(_strip_comments_ts(body_code).encode()).hexdigest(),
        code=text_of(
            call.parent if call.parent and call.parent.type == "expression_statement" else call
        ),
        asserts=0,
        eq_asserts=0,
        truthy_or_in_asserts=0,
        skips=set(skips),
    )
    if body is not None:
        _scan(fn, body)
    return fn


def _skip_markers(chain: list[str], scope: str) -> set[str]:
    found = {f"{scope}:{m}" for m in chain[1:] if m in SKIP_MODIFIERS}
    if chain[0].startswith("x"):
        found.add(f"{scope}:skip")
    return found


def analyse_test_file(text: str, path: str = "x.test.ts") -> TestFile | None:
    tree = parse(path, text)
    if tree is None:
        return None
    tests: dict[str, TestFunction] = {}

    def visit(node: Node, names: list[str], skips: set[str]) -> None:
        for child in node.named_children:
            if child.type == "call_expression":
                chain = callee_chain(child)
                if chain and chain[0] in SUITE_CALLS and _name_of_test(child) is not None:
                    inner = _callback(child)
                    marks = skips | _skip_markers(chain, "describe")
                    if inner is not None:
                        visit(inner, [*names, _name_of_test(child) or ""], marks)
                    continue
                if chain and chain[0] in TEST_CALLS and _name_of_test(child) is not None:
                    suffix = " > ".join([*names, _name_of_test(child) or ""])
                    tests[suffix] = _test_function(
                        child, suffix, skips | _skip_markers(chain, "test")
                    )
                    continue
            if child.type not in FUNCTION_NODES or not names:
                visit(child, names, skips)

    visit(tree.root_node, [], set())
    return TestFile(
        tests=tests, module_skips=frozenset(), literals=frozenset(test_literals(tree.root_node))
    )


def test_literals(root: Node) -> set[Literal]:
    """Literals of a test file that are inputs or expected values: test titles and module
    specifiers are names, not data, and are left out."""
    names: set[int] = set()
    for node in walk(root):
        if node.type == "call_expression":
            chain = callee_chain(node)
            args = call_args(node)
            if (
                chain
                and (chain[0] in TEST_CALLS | SUITE_CALLS or chain[0] in {"require", "vi", "jest"})
                and args
            ):
                names.add(args[0].id)
        elif node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            if source is not None:
                names.add(source.id)
    found: set[Literal] = set()
    for node in walk(root):
        if node.id in names:
            continue
        lit = literal_of(node)
        if lit is not None:
            found.add(lit)
    return found


# -- source symbols ----------------------------------------------------------------------


def _declared(node: Node) -> list[str]:
    """Names declared by a top-level statement (unwrapping ``export``)."""
    if node.type == "export_statement":
        decl = node.child_by_field_name("declaration")
        return _declared(decl) if decl is not None else []
    if node.type in {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }:
        name = node.child_by_field_name("name")
        return [text_of(name)] if name is not None else []
    if node.type in {"lexical_declaration", "variable_declaration"}:
        return [
            text_of(d.child_by_field_name("name"))
            for d in node.named_children
            if d.type == "variable_declarator"
            and d.child_by_field_name("name") is not None
            and d.child_by_field_name("name").type == "identifier"  # type: ignore[union-attr]
        ]
    return []


def top_level_definitions(text: str, path: str = "x.ts") -> set[str]:
    tree = parse(path, text)
    if tree is None:
        return set()
    return {n for stmt in tree.root_node.named_children for n in _declared(stmt) if n}


def defined_symbols(text: str, path: str = "x.ts") -> set[str]:
    """Names (functions, classes, variables, methods) and ``func(param)`` pairs."""
    tree = parse(path, text)
    if tree is None:
        return set()
    names: set[str] = set()
    for node in walk(tree.root_node):
        name_node = node.child_by_field_name("name")
        if node.type in FUNCTION_NODES | {"class_declaration", "variable_declarator"} and name_node:
            name = text_of(name_node)
            names.add(name)
            target = node
            if node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                target = value if value is not None and value.type in FUNCTION_NODES else node
            params = target.child_by_field_name("parameters")
            for p in params.named_children if params is not None else []:
                pname = p.child_by_field_name("pattern") or (p if p.type == "identifier" else None)
                if pname is not None and pname.type == "identifier":
                    names.add(f"{name}({text_of(pname)})")
    return names


def added_symbols(base_text: str | None, head_text: str, path: str = "x.ts") -> set[str]:
    return defined_symbols(head_text, path) - defined_symbols(base_text or "", path)


def module_name(path: str) -> str:
    """``src/cart/discount.ts`` -> ``src.cart.discount`` (``index`` files name their folder)."""
    pure = PurePosixPath(path)
    parts = [*pure.parent.parts, pure.name.split(".", 1)[0]]
    if parts[-1] == "index" and len(parts) > 1:
        parts.pop()
    return ".".join(p for p in parts if p not in {"", "."})


def import_specifiers(text: str, path: str = "x.ts") -> list[str]:
    """Module specifiers of ``import``/``export from``/``require``/dynamic ``import()``."""
    tree = parse_tree(path, text)
    found: list[str] = []
    for node in walk(tree.root_node):
        if node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            if source is not None:
                value = string_value(source)
                if value:
                    found.append(value)
        elif node.type == "call_expression":
            chain = callee_chain(node)
            func = node.child_by_field_name("function")
            is_import = func is not None and func.type == "import"
            if chain == ["require"] or is_import:
                args = call_args(node)
                if args and args[0].type == "string":
                    value = string_value(args[0])
                    if value:
                        found.append(value)
    return found


# -- functions ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    node_type: str


def function_spans(tree: Tree) -> list[Span]:
    return [
        Span(line_of(n), n.end_point.row + 1, n.type)
        for n in walk(tree.root_node)
        if n.type in FUNCTION_NODES
    ]


def enclosing_function(path: str, text: str, line: int) -> str:
    """Source of the innermost function containing ``line`` (the whole file if none)."""
    tree = parse_tree(path, text)
    best: Node | None = None
    for node in walk(tree.root_node):
        inside = node.type in FUNCTION_NODES and line_of(node) <= line <= node.end_point.row + 1
        if inside and (best is None or line_of(node) >= line_of(best)):
            best = node
    if best is None:
        return text
    # Include `export function` / `const f = (...) =>` so the snippet reads naturally.
    outer = best
    while outer.parent is not None and outer.parent.type in {
        "export_statement",
        "variable_declarator",
        "lexical_declaration",
    }:
        outer = outer.parent
    return text_of(outer)


def strip_comments(path: str, text: str) -> str:
    """Source without comments (for the equivalence judge), keeping line structure."""
    tree = parse_tree(path, text)
    raw = text.encode("utf-8")
    cuts = sorted((n.start_byte, n.end_byte) for n in walk(tree.root_node) if n.type == "comment")
    out = bytearray()
    pos = 0
    for start, end in cuts:
        out += raw[pos:start]
        pos = end
    out += raw[pos:]
    lines = [ln.rstrip() for ln in out.decode("utf-8", "replace").splitlines()]
    return "\n".join(ln for ln in lines if ln.strip()) + "\n"
