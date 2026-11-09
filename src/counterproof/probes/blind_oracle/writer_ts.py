"""OracleWriter validation for TypeScript and JavaScript (FR-BO-02), the twin of ``validate``.

Every test is LLM-written code that will run locally, so it is checked before it is written:
it must parse, be exactly one top-level ``test(...)``/``it(...)`` call, import only public
names of the modules in the interface (the test framework import is added by Counterproof,
never taken from the model), use no private member, and touch none of the globals that reach
outside the test (processes, files, network, timers, module mocking, dynamic code).
"""

from __future__ import annotations

import json
import re
from typing import Final

from tree_sitter import Node

from counterproof.llm.schemas import OracleOutput, OracleTest
from counterproof.probes import tsast
from counterproof.probes.blind_oracle.interface import Interface
from counterproof.probes.blind_oracle.writer import OracleCase, OracleSuite

FRAMEWORK_IMPORTS: Final = {
    "vitest": 'import { describe, expect, it, test } from "vitest";',
    "jest": "",
}
_FRAMEWORK_MODULES: Final = frozenset({"vitest", "@jest/globals"})
_BANNED_NAMES: Final = frozenset(
    {
        "require",
        "eval",
        "Function",
        "process",
        "globalThis",
        "global",
        "window",
        "document",
        "self",
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "setTimeout",
        "setInterval",
        "setImmediate",
        "queueMicrotask",
        "module",
        "exports",
        "__dirname",
        "__filename",
        "Buffer",
        "WebAssembly",
        "Worker",
        "SharedArrayBuffer",
        "Atomics",
        "Reflect",
        "Proxy",
        "vi",
        "jest",
        "Deno",
        "Bun",
    }
)
_BANNED_MEMBERS: Final = frozenset(
    {("Math", "random"), ("Date", "now"), ("performance", "now"), ("crypto", "getRandomValues")}
)
_BANNED_PROPERTIES: Final = frozenset({"constructor", "__proto__", "prototype"})
_GLOBALS: Final = frozenset(
    {
        "Math",
        "Number",
        "String",
        "Boolean",
        "Array",
        "Object",
        "JSON",
        "BigInt",
        "Symbol",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "Error",
        "RangeError",
        "TypeError",
        "SyntaxError",
        "ReferenceError",
        "Infinity",
        "NaN",
        "undefined",
        "isNaN",
        "isFinite",
        "parseInt",
        "parseFloat",
        "Intl",
        "Date",
        "Promise",
        "console",
        "structuredClone",
        "Uint8Array",
        "Int32Array",
        "Float64Array",
        "expect",
        "test",
        "it",
        "describe",
        "arguments",
    }
)
_INTENT_TS: Final = re.compile(r"^\s*//\s*intent:.*$", re.M | re.I)
_NAME: Final = re.compile(r"[^a-z0-9_]+")
_PATH: Final = "oracle.test.ts"


def _slug(text: str) -> str:
    return _NAME.sub("_", text.lower()).strip("_")[:60] or "case"


def _parse(code: str) -> Node | None:
    tree = tsast.parse(_PATH, code)
    return tree.root_node if tree is not None else None


def _import_parts(stmt: Node) -> tuple[str, list[str]] | None:
    """``(specifier, imported names)`` of an ES import or ``const {a} = require("x")``."""
    if stmt.type == "import_statement":
        source = stmt.child_by_field_name("source")
        spec = tsast.string_value(source) if source is not None else None
        if not spec:
            return None
        names: list[str] = []
        for node in tsast.walk(stmt):
            if node.type == "import_specifier":
                name = node.child_by_field_name("name")
                names.append(tsast.text_of(name))
            elif node.type == "namespace_import":
                names.append("*")
            elif node.type == "import_clause" and node.named_children:
                first = node.named_children[0]
                if first.type == "identifier":
                    names.append("default")
        return spec, names
    if stmt.type in {"lexical_declaration", "variable_declaration"}:
        decls = [d for d in stmt.named_children if d.type == "variable_declarator"]
        if len(decls) != 1:
            return None
        value = decls[0].child_by_field_name("value")
        pattern = decls[0].child_by_field_name("name")
        if (
            value is None
            or value.type != "call_expression"
            or tsast.callee_chain(value) != ["require"]
        ):
            return None
        args = tsast.call_args(value)
        spec = tsast.string_value(args[0]) if args and args[0].type == "string" else None
        if not spec or pattern is None or pattern.type != "object_pattern":
            return None
        names = [
            tsast.text_of(n.child_by_field_name("key") or n)
            for n in pattern.named_children
            if n.type in {"shorthand_property_identifier_pattern", "pair_pattern"}
        ]
        return spec, names
    return None


def _bound_names(stmt: Node) -> set[str]:
    """Local names an import statement binds."""
    names: set[str] = set()
    for node in tsast.walk(stmt):
        if node.type == "import_specifier":
            alias = node.child_by_field_name("alias") or node.child_by_field_name("name")
            names.add(tsast.text_of(alias))
        elif node.type in {"namespace_import", "import_clause"}:
            names |= {tsast.text_of(c) for c in node.named_children if c.type == "identifier"}
        elif node.type == "shorthand_property_identifier_pattern":
            names.add(tsast.text_of(node))
        elif node.type == "pair_pattern":
            value = node.child_by_field_name("value")
            if value is not None and value.type == "identifier":
                names.add(tsast.text_of(value))
    return names


def check_import(line: str, interface: Interface) -> tuple[str | None, set[str], bool]:
    """``(problem, bound names, is_framework_import)`` for one model-written import."""
    root = _parse(line)
    if root is None or len(root.named_children) != 1:
        return f"not a single import: {line!r}", set(), False
    stmt = root.named_children[0]
    parts = _import_parts(stmt)
    if parts is None:
        return f"not an import: {line!r}", set(), False
    spec, names = parts
    if spec in _FRAMEWORK_MODULES:
        return None, set(), True
    exposed = interface.public_names.get(spec)
    if exposed is None:
        return f"imports {spec}, which is not in the interface", set(), False
    if "*" in names or "default" in names:
        return f"namespace or default import from {spec}", set(), False
    private = [n for n in names if n.startswith("_")]
    if private:
        return f"imports private name {private[0]}", set(), False
    missing = [n for n in names if n not in exposed]
    if missing:
        return f"imports {', '.join(missing)} from {spec}: not in the interface", set(), False
    return None, _bound_names(stmt), False


def _declared_in(root: Node) -> set[str]:
    names: set[str] = set()

    def collect(pattern: Node | None) -> None:
        if pattern is None:
            return
        for node in tsast.walk(pattern):
            if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
                parent = node.parent
                if (
                    parent is not None
                    and parent.type == "pair_pattern"
                    and parent.child_by_field_name("key") == node
                ):
                    continue
                names.add(tsast.text_of(node))

    for node in tsast.walk(root):
        kind = node.type
        if kind == "variable_declarator":
            collect(node.child_by_field_name("name"))
        elif kind in {"required_parameter", "optional_parameter"}:
            collect(node.child_by_field_name("pattern"))
        elif kind in {"arrow_function", "catch_clause"}:
            collect(node.child_by_field_name("parameter"))
        elif kind == "for_in_statement":
            collect(node.child_by_field_name("left"))
        elif kind in {"function_declaration", "class_declaration", "function_expression"}:
            name = node.child_by_field_name("name")
            if name is not None:
                names.add(tsast.text_of(name))
        elif kind == "formal_parameters":
            for child in node.named_children:
                if child.type == "identifier":
                    names.add(tsast.text_of(child))
    return names


def _check_code(root: Node, available: set[str]) -> str | None:
    bound = available | _GLOBALS | _declared_in(root)
    for node in tsast.walk(root):
        kind = node.type
        if kind in {"import_statement", "import"}:
            return "imports inside a test"
        if kind == "identifier":
            name = tsast.text_of(node)
            if name in _BANNED_NAMES:
                return f"uses {name}"
            if name not in bound:
                return f"uses undefined name {name}"
        elif kind == "shorthand_property_identifier":
            name = tsast.text_of(node)
            if name not in bound:
                return f"uses undefined name {name}"
        elif kind == "private_property_identifier":
            return f"uses private member {tsast.text_of(node)}"
        elif kind == "property_identifier":
            prop = tsast.text_of(node)
            if prop.startswith("_") or prop in _BANNED_PROPERTIES:
                return f"uses property {prop}"
            parent = node.parent
            obj = parent.child_by_field_name("object") if parent is not None else None
            if (
                parent is not None
                and parent.type == "member_expression"
                and obj is not None
                and (tsast.text_of(obj), prop) in _BANNED_MEMBERS
            ):
                return f"uses {tsast.text_of(obj)}.{prop}"
        elif kind == "subscript_expression":
            index = node.child_by_field_name("index")
            if index is not None and index.type in tsast.STRING_NODES:
                return "uses a computed member access"
    return None


def _case(
    test: OracleTest, available: set[str], seen: set[str], model: str
) -> tuple[OracleCase | None, str | None]:
    code = _INTENT_TS.sub("", test.code).strip("\n")
    root = _parse(code)
    if root is None:
        return None, "syntax error"
    statements = [n for n in root.named_children if n.type != "comment"]
    if len(statements) != 1 or statements[0].type != "expression_statement":
        return None, "must be exactly one top-level test(...) call"
    call = statements[0].named_children[0] if statements[0].named_children else None
    if (
        call is None
        or call.type != "call_expression"
        or tsast.callee_chain(call)
        not in (
            ["test"],
            ["it"],
        )
    ):
        return None, "must be exactly one top-level test(...) call"
    args = tsast.call_args(call)
    if len(args) < 2 or args[0].type not in tsast.STRING_NODES:
        return None, "the test needs a string name and a body"
    problem = _check_code(root, available)
    if problem:
        return None, problem
    original = tsast.string_value(args[0]) or test.name
    name = f"test_oracle_{_slug(original.removeprefix('test_').removeprefix('test') or test.name)}"
    while name in seen:
        name += "_2"
    raw = code.encode("utf-8")
    renamed = (
        raw[: args[0].start_byte] + json.dumps(name).encode() + raw[args[0].end_byte :]
    ).decode()
    intent = " ".join(test.intent.split())[:200]
    return OracleCase(
        name=name, intent=intent, code=f"// intent: {intent}\n{renamed}\n", model=model
    ), None


def validate_ts(
    output: OracleOutput, interface: Interface, *, k: int, framework: str, model: str = ""
) -> OracleSuite:
    imports: list[str] = []
    rejected: list[tuple[str, str]] = []
    available: set[str] = set()
    header_import = FRAMEWORK_IMPORTS.get(framework, "")
    for line in output.imports:
        stmt = line.strip()
        problem, names, is_framework = check_import(stmt, interface)
        if problem:
            rejected.append(("<imports>", problem))
            continue
        if is_framework:
            continue
        available |= names
        if stmt not in imports:
            imports.append(stmt)
    cases: list[OracleCase] = []
    seen: set[str] = set()
    for test in output.tests:
        case, problem = _case(test, available, seen, model)
        if case is None:
            rejected.append((test.name, problem or "invalid"))
            continue
        seen.add(case.name)
        cases.append(case)
        if len(cases) == k:
            break
    full_imports = [i for i in (header_import, *imports) if i]
    suite = OracleSuite(imports=full_imports, cases=cases, rejected=rejected, language="typescript")
    if _parse(suite.text("check")) is None:
        return OracleSuite(
            imports=[],
            cases=[],
            rejected=[*rejected, ("<file>", "syntax error")],
            language="typescript",
        )
    return suite
