"""InterfaceExtractor for TypeScript and JavaScript (FR-BO-01), the twin of :mod:`interface`.

For every changed TS/JS source module: a stub with the leading file comment, its imports,
exported types and interfaces (kept whole: they hold no behaviour), exported constants with
literal values, exported classes (public members) and exported functions, each function body
replaced by ``{ /* body hidden */ }`` with its doc comment kept. Symbols the change touched
are marked ``// changed``. Relative imports of the changed modules are stubbed too, so the
oracle can build inputs. Module keys are import specifiers as the oracle test will write
them: relative to the directory the oracle file runs in (``../src/cart/money``).

No function body of any symbol ever reaches the result (unit-tested on every fixture).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import Final

from tree_sitter import Node

from counterproof.models import ChangeSet
from counterproof.probes import tsast
from counterproof.probes.blind_oracle.interface import (
    MAX_INTERFACE_CHARS,
    STYLE_CHARS,
    STYLE_LINES,
    Interface,
    ReadFile,
    changed_lines,
)

CHANGED_TS: Final = "// changed"
HIDDEN: Final = "{ /* body hidden */ }"
_RESOLVE_SUFFIXES: Final = (
    "",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    "/index.ts",
    "/index.tsx",
    "/index.js",
)
_KEEP_WHOLE: Final = frozenset(
    {"interface_declaration", "type_alias_declaration", "enum_declaration"}
)
_CLASSES: Final = frozenset({"class_declaration", "abstract_class_declaration"})
_FUNCTIONS: Final = frozenset({"function_declaration", "generator_function_declaration"})
_SIMPLE_VALUES: Final = frozenset(
    {
        "number",
        "string",
        "template_string",
        "true",
        "false",
        "null",
        "undefined",
        "unary_expression",
        "array",
        "object",
        "as_expression",
        "satisfies_expression",
    }
)


def _always(_path: str) -> bool:
    return True


def _norm(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    return "/".join(parts)


def resolve(from_file: str, specifier: str, read: ReadFile) -> str | None:
    """The repository path a relative specifier names, or ``None`` (bare packages too)."""
    if not specifier.startswith("."):
        return None
    joined = _norm(PurePosixPath(from_file).parent / specifier)
    stem = joined.removesuffix(".js") if joined.endswith(".js") else joined
    for base in dict.fromkeys((joined, stem)):
        for suffix in _RESOLVE_SUFFIXES:
            candidate = f"{base}{suffix}"
            if tsast.is_ts_path(candidate) and read(candidate) is not None:
                return candidate
    return None


def specifier_from(test_dir: str, path: str) -> str:
    """``tests`` + ``src/cart/money.ts`` -> ``../src/cart/money`` (no extension)."""
    target = PurePosixPath(path)
    stem = target.name.split(".", 1)[0]
    target_parts = [*PurePosixPath(_norm(target.parent)).parts, stem]
    if target_parts[-1] == "index" and len(target_parts) > 1:
        target_parts.pop()
    here = list(PurePosixPath(_norm(PurePosixPath(test_dir))).parts) if test_dir else []
    common = 0
    while common < min(len(here), len(target_parts)) and here[common] == target_parts[common]:
        common += 1
    ups = [".."] * (len(here) - common)
    rel = "/".join([*ups, *target_parts[common:]])
    return rel if ups else f"./{rel}"


def _leading_doc(node: Node) -> str:
    """The ``/** ... */`` comment directly above a declaration, if any."""
    prev = node.prev_named_sibling
    if (
        prev is not None
        and prev.type == "comment"
        and prev.end_point.row + 1 >= node.start_point.row
    ):
        text = tsast.text_of(prev)
        if text.startswith("/**"):
            return text + "\n"
    return ""


def _without_body(node: Node, body: Node | None, source: bytes) -> str:
    if body is None:
        return tsast.text_of(node)
    head = source[node.start_byte : body.start_byte].decode("utf-8", "replace").rstrip()
    return f"{head} {HIDDEN}"


def _hit(node: Node, touched: set[int]) -> bool:
    start, end = node.start_point.row + 1, node.end_point.row + 1
    return any(start <= line <= end for line in touched)


class _StubberTS:
    def __init__(self, path: str, text: str, touched: set[int]) -> None:
        self.path = path
        self.source = text.encode("utf-8")
        self.tree = tsast.parse(path, text)
        self.touched = touched
        self.changed: list[str] = []
        self.imports: list[str] = []

    def _mark(self, changed: bool) -> str:
        return f"{CHANGED_TS}\n" if changed else ""

    def _class(self, node: Node, prefix: str) -> str:
        name = tsast.text_of(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        changed_here = False
        members: list[str] = []
        for member in body.named_children if body is not None else []:
            if member.type == "comment":
                continue
            text = tsast.text_of(member)
            if any(text.startswith(p) for p in ("private ", "#", "protected ")) or (
                member.child_by_field_name("name") is not None
                and tsast.text_of(member.child_by_field_name("name")).startswith(("#", "_"))
            ):
                continue
            hit = _hit(member, self.touched)
            if member.type == "method_definition":
                if hit:
                    self.changed.append(
                        f"{name}.{tsast.text_of(member.child_by_field_name('name'))}"
                    )
                members.append(
                    _leading_doc(member)
                    + self._mark(hit)
                    + _without_body(member, member.child_by_field_name("body"), self.source)
                )
            elif member.type in {"public_field_definition", "field_definition"}:
                value = member.child_by_field_name("value")
                if value is not None and value.type not in _SIMPLE_VALUES:
                    head = self.source[member.start_byte : value.start_byte].decode()
                    text = head.rstrip().removesuffix("=").rstrip()
                members.append(_leading_doc(member) + text.rstrip(";") + ";")
                changed_here = changed_here or hit
            else:
                changed_here = changed_here or hit
        if changed_here or (_hit(node, self.touched) and not members):
            self.changed.append(name)
        header = self.source[node.start_byte : body.start_byte].decode() if body else ""
        inner = "\n".join("  " + line for m in members for line in m.splitlines())
        return f"{prefix}{header.rstrip()} {{\n{inner}\n}}"

    def _lexical(self, node: Node, prefix: str) -> tuple[str, list[str]] | None:
        out: list[str] = []
        names: list[str] = []
        keyword = tsast.text_of(node.children[0]) if node.children else "const"
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            name_node = decl.child_by_field_name("name")
            if name_node is None or name_node.type != "identifier":
                continue
            name = tsast.text_of(name_node)
            names.append(name)
            value = decl.child_by_field_name("value")
            hit = _hit(decl, self.touched)
            if value is not None and value.type in {"arrow_function", "function_expression"}:
                if hit:
                    self.changed.append(name)
                body = value.child_by_field_name("body")
                head = self.source[decl.start_byte : body.start_byte].decode() if body else ""
                arrow = head.rstrip()
                text = f"{arrow} {HIDDEN}" if body is not None else tsast.text_of(decl)
                out.append(self._mark(hit) + f"{prefix}{keyword} {text};")
            elif value is None or value.type in _SIMPLE_VALUES:
                if hit:
                    self.changed.append(name)
                out.append(f"{prefix}{keyword} {tsast.text_of(decl)};")
            else:  # computed at import time: keep the type, hide how
                type_node = decl.child_by_field_name("type")
                annotation = tsast.text_of(type_node) if type_node is not None else ""
                if hit:
                    self.changed.append(name)
                out.append(f"{prefix}declare {keyword} {name}{annotation};")
        return ("\n".join(out), names) if out else None

    def _declaration(self, node: Node, prefix: str) -> tuple[str, list[str]] | None:
        kind = node.type
        if kind in _KEEP_WHOLE:
            name = tsast.text_of(node.child_by_field_name("name"))
            if _hit(node, self.touched):
                self.changed.append(name)
            return prefix + tsast.text_of(node), [name]
        if kind in _FUNCTIONS:
            name = tsast.text_of(node.child_by_field_name("name"))
            hit = _hit(node, self.touched)
            if hit:
                self.changed.append(name)
            stub = _without_body(node, node.child_by_field_name("body"), self.source)
            return self._mark(hit) + prefix + stub, [name]
        if kind in _CLASSES:
            name = tsast.text_of(node.child_by_field_name("name"))
            return self._class(node, prefix), [name]
        if kind in {"lexical_declaration", "variable_declaration"}:
            return self._lexical(node, prefix)
        return None

    def module(self) -> tuple[str, frozenset[str]]:
        if self.tree is None:
            return "", frozenset()
        root = self.tree.root_node
        out: list[str] = []
        names: set[str] = set()
        children = list(root.named_children)
        commonjs = _commonjs_exports(children)
        if children and children[0].type == "comment":
            follower = children[1] if len(children) > 1 else None
            documents = follower is not None and (
                follower.type == "export_statement"
                or bool(set(_declared_names(follower)) & set(commonjs.values()))
            )
            if not (documents and follower is not None and _leading_doc(follower)):
                out.append(tsast.text_of(children[0]))  # else the declaration repeats it
        for stmt in children:
            if stmt.type == "import_statement":
                out.append(tsast.text_of(stmt))
                source = stmt.child_by_field_name("source")
                if source is not None:
                    self.imports.append(tsast.string_value(source) or "")
                continue
            required = _required(stmt)
            if required is not None:
                out.append(tsast.text_of(stmt))
                self.imports.append(required)
                continue
            if commonjs and stmt.type != "export_statement":
                local = set(_declared_names(stmt)) & set(commonjs.values())
                if local:
                    rendered = self._declaration(stmt, "")
                    if rendered is not None:
                        out.append(_leading_doc(stmt) + rendered[0])
                elif _is_commonjs_export(stmt) and not names >= set(commonjs):
                    out.append(_render_commonjs(commonjs))  # once, however many assignments
                    names |= set(commonjs)
                continue
            if stmt.type != "export_statement":
                continue
            decl = stmt.child_by_field_name("declaration")
            if decl is None:  # export { a, b } from "./x" / export { a }
                out.append(tsast.text_of(stmt))
                source = stmt.child_by_field_name("source")
                if source is not None:
                    self.imports.append(tsast.string_value(source) or "")
                names |= _export_clause_names(stmt)
                continue
            prefix = "export default " if "default" in _keywords(stmt) else "export "
            rendered = self._declaration(decl, prefix)
            if rendered is None:
                continue
            text, declared = rendered
            out.append(_leading_doc(stmt) + text)
            names |= set(declared)
        return "\n\n".join(out) + "\n", frozenset(n for n in names if n)


def _declared_names(stmt: Node) -> list[str]:
    if stmt.type in _FUNCTIONS | _CLASSES:
        name = stmt.child_by_field_name("name")
        return [tsast.text_of(name)] if name is not None else []
    if stmt.type in {"lexical_declaration", "variable_declaration"}:
        return [
            tsast.text_of(d.child_by_field_name("name"))
            for d in stmt.named_children
            if d.type == "variable_declarator" and d.child_by_field_name("name") is not None
        ]
    return []


def _required(stmt: Node) -> str | None:
    """The specifier of ``const { a } = require("./x");``, else ``None``."""
    if stmt.type not in {"lexical_declaration", "variable_declaration"}:
        return None
    for decl in stmt.named_children:
        value = decl.child_by_field_name("value") if decl.type == "variable_declarator" else None
        if (
            value is not None
            and value.type == "call_expression"
            and tsast.callee_chain(value) == ["require"]
        ):
            args = tsast.call_args(value)
            if args and args[0].type == "string":
                return tsast.string_value(args[0])
    return None


def _assignment(stmt: Node) -> tuple[str, Node] | None:
    """``("module.exports", right)`` / ``("exports.x", right)`` for an export assignment."""
    if stmt.type != "expression_statement" or not stmt.named_children:
        return None
    expr = stmt.named_children[0]
    if expr.type != "assignment_expression":
        return None
    left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
    if left is None or right is None:
        return None
    target = tsast.text_of(left).replace(" ", "")
    if target == "module.exports" or target.startswith(("module.exports.", "exports.")):
        return target, right
    return None


def _is_commonjs_export(stmt: Node) -> bool:
    return _assignment(stmt) is not None


def _commonjs_exports(children: Sequence[Node]) -> dict[str, str]:
    """Exported name -> local name, from ``module.exports = {...}`` and ``exports.x = y``."""
    exported: dict[str, str] = {}
    for stmt in children:
        found = _assignment(stmt)
        if found is None:
            continue
        target, right = found
        if target == "module.exports":
            if right.type == "object":
                for prop in right.named_children:
                    if prop.type == "shorthand_property_identifier":
                        exported[tsast.text_of(prop)] = tsast.text_of(prop)
                    elif prop.type == "pair":
                        key, value = (
                            prop.child_by_field_name("key"),
                            prop.child_by_field_name("value"),
                        )
                        if key is not None and value is not None and value.type == "identifier":
                            exported[tsast.text_of(key)] = tsast.text_of(value)
            elif right.type == "identifier":
                exported["default"] = tsast.text_of(right)
        elif right.type == "identifier":
            exported[target.rsplit(".", 1)[-1]] = tsast.text_of(right)
    return exported


def _render_commonjs(exported: dict[str, str]) -> str:
    pairs = [k if k == v else f"{k}: {v}" for k, v in exported.items() if k != "default"]
    return "module.exports = { " + ", ".join(pairs) + " };"


def _keywords(stmt: Node) -> set[str]:
    return {tsast.text_of(c) for c in stmt.children if not c.is_named}


def _export_clause_names(stmt: Node) -> set[str]:
    names: set[str] = set()
    for node in tsast.walk(stmt):
        if node.type == "export_specifier":
            alias = node.child_by_field_name("alias") or node.child_by_field_name("name")
            if alias is not None:
                names.add(tsast.text_of(alias))
    return names


def _style(
    selected: Sequence[str], changed: Sequence[str], read_base: ReadFile
) -> tuple[str | None, str]:
    """One existing test file from the base commit (never the agent's version of it)."""
    best: tuple[int, str, str] | None = None
    stems = {PurePosixPath(p).name.split(".", 1)[0] for p in changed}
    for path in sorted(p for p in selected if tsast.is_ts_path(p)):
        text = read_base(path)
        if text is None:
            continue
        score = 0
        if PurePosixPath(path).name.split(".", 1)[0] in stems:
            score += 2
        if any(stem in text for stem in stems):
            score += 1
        if best is None or score > best[0]:
            best = (score, path, text)
    if best is None:
        return None, ""
    lines = best[2].splitlines()
    excerpt = "\n".join(lines[:STYLE_LINES])[:STYLE_CHARS]
    if len(lines) > STYLE_LINES or len(excerpt) < len("\n".join(lines)):
        excerpt += "\n// ... (truncated)"
    return best[1], excerpt + "\n"


def extract_interface_ts(
    change: ChangeSet,
    *,
    read_head: ReadFile,
    read_base: ReadFile,
    selected_test_files: Sequence[str],
    sendable: Callable[[str], bool] = _always,
    framework: str = "vitest",
) -> Interface:
    """The TS/JS interface; ``Interface.modules`` keys are specifiers from ``test_dir``."""
    stubs: dict[str, str] = {}
    changed_symbols: dict[str, list[str]] = {}
    public: dict[str, frozenset[str]] = {}
    pending: list[tuple[str, str]] = []
    changed_paths: list[str] = []
    for f in change.files:
        if f.role != "source" or f.status == "deleted" or not tsast.is_ts_path(f.path):
            continue
        text = read_head(f.path) if sendable(f.path) else None
        if text is None or tsast.parse(f.path, text) is None:
            continue
        stubber = _StubberTS(f.path, text, changed_lines(change.hunks, f.path))
        stub, names = stubber.module()
        stubs[f.path] = stub
        public[f.path] = names
        changed_symbols[f.path] = list(dict.fromkeys(stubber.changed))
        changed_paths.append(f.path)
        pending += [(f.path, s) for s in stubber.imports if s]

    budget = MAX_INTERFACE_CHARS - sum(len(t) for t in stubs.values())
    for origin, spec in dict.fromkeys(pending):
        path = resolve(origin, spec, read_head)
        if path is None or path in stubs or not sendable(path):
            continue
        text = read_head(path)
        if text is None or tsast.parse(path, text) is None:
            continue
        stub, names = _StubberTS(path, text, set()).module()
        if len(stub) > budget:
            continue
        budget -= len(stub)
        stubs[path] = stub
        public[path] = names

    sendable_tests = [p for p in selected_test_files if sendable(p)]
    style_path, style = _style(sendable_tests, changed_paths, read_base)
    test_dir = (
        PurePosixPath(style_path).parent.as_posix()
        if style_path
        else PurePosixPath(sendable_tests[0]).parent.as_posix()
        if sendable_tests
        else "tests"
    )

    def key(path: str) -> str:
        return specifier_from(test_dir, path)

    return Interface(
        modules={key(p): t for p, t in stubs.items()},
        changed_symbols={key(p): s for p, s in changed_symbols.items()},
        public_names={key(p): n for p, n in public.items()},
        style_path=style_path,
        style_example=style,
        test_dir=test_dir,
        language="typescript",
        file_suffix=_oracle_suffix(style_path, changed_paths),
        framework=framework,
    )


def _oracle_suffix(style_path: str | None, changed: Sequence[str]) -> str:
    """``.test.ts`` / ``.test.js`` / ``.test.mjs``: the style file's flavour of test file."""
    reference = style_path or (changed[0] if changed else "x.ts")
    name = PurePosixPath(reference).name
    ext = name.rsplit(".", 1)[-1] if "." in name else "ts"
    return f".test.{ext}"


def changed_function_sources_ts(
    change: ChangeSet, read_head: ReadFile, *, sendable: Callable[[str], bool] = _always
) -> dict[str, str]:
    """``name -> head source`` of changed TS/JS functions. For the triage judge only."""
    out: dict[str, str] = {}
    for f in change.files:
        if f.role != "source" or f.status == "deleted" or not tsast.is_ts_path(f.path):
            continue
        text = read_head(f.path) if sendable(f.path) else None
        if text is None:
            continue
        tree = tsast.parse(f.path, text)
        if tree is None:
            continue
        touched = changed_lines(change.hunks, f.path)
        for node in tsast.walk(tree.root_node):
            if node.type in _FUNCTIONS | {"method_definition"}:
                name_node = node.child_by_field_name("name")
            elif node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                if value is None or value.type not in {"arrow_function", "function_expression"}:
                    continue
                name_node = node.child_by_field_name("name")
            else:
                continue
            if name_node is not None and _hit(node, touched):
                out.setdefault(tsast.text_of(name_node), tsast.text_of(node))
    return out
