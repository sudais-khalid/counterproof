"""InterfaceExtractor (FR-BO-01): what a careful tester sees before the code exists.

For every changed Python source module: a stub with the module docstring, imports, public
constants, public classes (docstrings, fields, public and dunder methods) and public
functions, with every function body replaced by ``...`` (docstrings kept). Symbols the
change touched are marked ``# changed``. First-party modules the changed ones import are
added as stubs too, so the oracle can build inputs (for example a ``Money`` value). One
existing test file, read from the base commit and truncated, shows the repository's style.

No function body of any symbol ever reaches the result (unit-tested on every fixture).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from counterproof.models import ChangeSet, Hunk
from counterproof.probes.pyast import module_name

MAX_INTERFACE_CHARS: Final = 12_000
STYLE_LINES: Final = 60
STYLE_CHARS: Final = 3_000
CHANGED: Final = "# changed"
_ROOTS: Final = ("src/", "")

ReadFile = Callable[[str], str | None]


def _always(_path: str) -> bool:
    return True


@dataclass(frozen=True)
class Interface:
    modules: dict[str, str] = field(default_factory=dict)
    """Import name -> stub source, changed modules first."""
    changed_symbols: dict[str, list[str]] = field(default_factory=dict)
    public_names: dict[str, frozenset[str]] = field(default_factory=dict)
    """Names each included module exposes: what oracle tests may import."""
    style_path: str | None = None
    style_example: str = ""
    test_dir: str = "tests"
    """Where oracle tests run (the style example's directory, so conftest files apply)."""

    def render(self) -> str:
        return "\n\n".join(
            f"# module {name}\n{text.rstrip()}" for name, text in self.modules.items()
        )

    @property
    def empty(self) -> bool:
        return not self.changed_symbols


def changed_lines(hunks: Iterable[Hunk], path: str) -> set[int]:
    """Head-side line numbers a hunk adds, or next to which it deletes."""
    lines: set[int] = set()
    for hunk in hunks:
        if hunk.path != path:
            continue
        current = hunk.new_start
        for text in hunk.lines:
            if text.startswith("+"):
                lines.add(current)
                current += 1
            elif text.startswith("-"):
                lines.add(max(current, 1))
            elif text.startswith("\\"):
                continue
            else:
                current += 1
    return lines


def _public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _docstring(body: cst.BaseSuite) -> cst.SimpleStatementLine | None:
    if not isinstance(body, cst.IndentedBlock) or not body.body:
        return None
    first = body.body[0]
    if (
        isinstance(first, cst.SimpleStatementLine)
        and len(first.body) == 1
        and isinstance(first.body[0], cst.Expr)
        and isinstance(first.body[0].value, cst.SimpleString | cst.ConcatenatedString)
    ):
        return first
    return None


def _ellipsis() -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine([cst.Expr(cst.Ellipsis())])


def _mark(node: cst.FunctionDef | cst.ClassDef, changed: bool) -> list[cst.EmptyLine]:
    lines = [ln for ln in node.leading_lines if ln.comment is None]
    if changed:
        lines.append(cst.EmptyLine(comment=cst.Comment(CHANGED)))
    return lines


def _assigned_names(stmt: cst.SimpleStatementLine) -> list[str] | None:
    """Target names when every statement on the line is a plain assignment."""
    names: list[str] = []
    for small in stmt.body:
        if isinstance(small, cst.Assign):
            targets = [t.target for t in small.targets]
        elif isinstance(small, cst.AnnAssign):
            targets = [small.target]
        else:
            return None
        for target in targets:
            if not isinstance(target, cst.Name):
                return None
            names.append(target.value)
    return names


class _Stubber:
    def __init__(self, module: cst.Module, touched: set[int]) -> None:
        self.wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
        self.positions = self.wrapper.resolve(PositionProvider)
        self.touched = touched
        self.changed: list[str] = []

    def _hit(self, node: cst.CSTNode) -> bool:
        pos = self.positions.get(node)
        if pos is None:
            return False
        return any(pos.start.line <= line <= pos.end.line for line in self.touched)

    def function(self, node: cst.FunctionDef, qual: str) -> cst.FunctionDef:
        changed = self._hit(node)
        if changed:
            self.changed.append(qual)
        doc = _docstring(node.body)
        body = cst.IndentedBlock(body=[*([doc] if doc else []), _ellipsis()])
        return node.with_changes(body=body, leading_lines=_mark(node, changed))

    def klass(self, node: cst.ClassDef) -> cst.ClassDef:
        changed_here = False
        kept: list[cst.BaseStatement] = []
        doc = _docstring(node.body)
        if doc is not None:
            kept.append(doc)
        body = node.body.body if isinstance(node.body, cst.IndentedBlock) else ()
        for stmt in body:
            if stmt is doc:
                continue
            if isinstance(stmt, cst.FunctionDef) and _public(stmt.name.value):
                kept.append(self.function(stmt, f"{node.name.value}.{stmt.name.value}"))
            elif isinstance(stmt, cst.SimpleStatementLine):
                names = _assigned_names(stmt)
                if names and all(_public(n) for n in names):
                    kept.append(stmt)
                    if self._hit(stmt):
                        changed_here = True
        if changed_here:
            self.changed.append(node.name.value)
        if len(kept) == (1 if doc is not None else 0):
            kept.append(_ellipsis())
        return node.with_changes(
            body=cst.IndentedBlock(body=kept), leading_lines=_mark(node, changed_here)
        )

    def module(self) -> tuple[str, frozenset[str]]:
        module = self.wrapper.module
        out: list[cst.BaseStatement] = []
        names: set[str] = set()
        for i, stmt in enumerate(module.body):
            if isinstance(stmt, cst.FunctionDef):
                if _public(stmt.name.value):
                    out.append(self.function(stmt, stmt.name.value))
                    names.add(stmt.name.value)
            elif isinstance(stmt, cst.ClassDef):
                if _public(stmt.name.value):
                    out.append(self.klass(stmt))
                    names.add(stmt.name.value)
            elif isinstance(stmt, cst.SimpleStatementLine):
                if i == 0 and _docstring(cst.IndentedBlock(body=[stmt])) is not None:
                    out.append(stmt)
                elif all(isinstance(s, cst.Import | cst.ImportFrom) for s in stmt.body):
                    out.append(stmt)
                    names |= {n for n in _imported_names(stmt) if not n.startswith("_")}
                else:
                    assigned = _assigned_names(stmt)
                    if assigned and all(_public(n) for n in assigned):
                        out.append(stmt)
                        names |= set(assigned)
                        if self._hit(stmt):
                            self.changed += assigned
        stub = module.with_changes(body=out, header=[], footer=[])
        return stub.code, frozenset(names)


def _imported_names(stmt: cst.SimpleStatementLine) -> list[str]:
    names: list[str] = []
    for small in stmt.body:
        if isinstance(small, cst.ImportFrom) and not isinstance(small.names, cst.ImportStar):
            for alias in small.names:
                target = alias.asname.name if alias.asname else alias.name
                if isinstance(target, cst.Name):
                    names.append(target.value)
    return names


def _imported_modules(module: cst.Module, current: str) -> list[str]:
    """Absolute names of the modules a module imports from (``from x import y``)."""
    found: list[str] = []
    package = current.rsplit(".", 1)[0] if "." in current else ""
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if isinstance(small, cst.ImportFrom):
                name = _dotted(small.module) if small.module is not None else ""
                level = len(small.relative)
                if level:
                    base = package.split(".") if package else []
                    base = base[: len(base) - (level - 1)] if level > 1 else base
                    name = ".".join([*base, *([name] if name else [])])
                if name:
                    found.append(name)
            elif isinstance(small, cst.Import):
                found += [_dotted(a.name) for a in small.names]
    return found


def _dotted(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


def module_path(name: str, read: ReadFile) -> str | None:
    rel = name.replace(".", "/")
    for root in _ROOTS:
        for candidate in (f"{root}{rel}.py", f"{root}{rel}/__init__.py"):
            if read(candidate) is not None:
                return candidate
    return None


def _style(
    selected_files: Sequence[str], changed_modules: Sequence[str], read_base: ReadFile
) -> tuple[str | None, str]:
    """One existing test file from the base commit (never the agent's version of it)."""
    best: tuple[int, str, str] | None = None
    stems = {m.rsplit(".", 1)[-1] for m in changed_modules}
    for path in sorted(selected_files):
        text = read_base(path)
        if text is None:
            continue
        score = 0
        if any(m in text for m in changed_modules):
            score += 2
        if PurePosixPath(path).stem.removeprefix("test_") in stems:
            score += 1
        if best is None or score > best[0]:
            best = (score, path, text)
    if best is None:
        return None, ""
    lines = best[2].splitlines()
    excerpt = "\n".join(lines[:STYLE_LINES])[:STYLE_CHARS]
    if len(lines) > STYLE_LINES or len(excerpt) < len("\n".join(lines)):
        excerpt += "\n# ... (truncated)"
    return best[1], excerpt + "\n"


def extract_interface(
    change: ChangeSet,
    *,
    read_head: ReadFile,
    read_base: ReadFile,
    selected_test_files: Sequence[str],
    sendable: Callable[[str], bool] = _always,
) -> Interface:
    """``sendable`` filters paths that must never reach a provider (excluded, secrets)."""
    modules: dict[str, str] = {}
    changed_symbols: dict[str, list[str]] = {}
    public: dict[str, frozenset[str]] = {}
    imports: list[str] = []
    for f in change.files:
        if f.role != "source" or f.status == "deleted" or not f.path.endswith(".py"):
            continue
        text = read_head(f.path) if sendable(f.path) else None
        if text is None:
            continue
        try:
            parsed = cst.parse_module(text)
        except cst.ParserSyntaxError:
            continue
        name = module_name(f.path)
        stubber = _Stubber(parsed, changed_lines(change.hunks, f.path))
        stub, names = stubber.module()
        modules[name] = stub
        public[name] = names
        changed_symbols[name] = list(dict.fromkeys(stubber.changed))
        imports += _imported_modules(parsed, name)

    budget = MAX_INTERFACE_CHARS - sum(len(t) for t in modules.values())
    for dep in dict.fromkeys(imports):
        if dep in modules:
            continue
        path = module_path(dep, read_head)
        text = read_head(path) if path and sendable(path) else None
        if text is None:
            continue
        try:
            stub, names = _Stubber(cst.parse_module(text), set()).module()
        except cst.ParserSyntaxError:
            continue
        if len(stub) > budget:
            continue
        budget -= len(stub)
        modules[dep] = stub
        public[dep] = names

    style_path, style = _style(
        [p for p in selected_test_files if sendable(p)], list(changed_symbols), read_base
    )
    test_dir = (
        PurePosixPath(style_path).parent.as_posix()
        if style_path
        else PurePosixPath(selected_test_files[0]).parent.as_posix()
        if selected_test_files
        else "tests"
    )
    return Interface(
        modules=modules,
        changed_symbols=changed_symbols,
        public_names=public,
        style_path=style_path,
        style_example=style,
        test_dir=test_dir,
    )


def changed_function_sources(
    change: ChangeSet, read_head: ReadFile, *, sendable: Callable[[str], bool] = _always
) -> dict[str, str]:
    """``qualified name -> head source`` of changed functions. For the triage judge only:
    it is never part of the oracle writer's prompt."""
    out: dict[str, str] = {}
    for f in change.files:
        if f.role != "source" or f.status == "deleted" or not f.path.endswith(".py"):
            continue
        text = read_head(f.path) if sendable(f.path) else None
        if text is None:
            continue
        try:
            wrapper = MetadataWrapper(cst.parse_module(text), unsafe_skip_copy=True)
        except cst.ParserSyntaxError:
            continue
        positions = wrapper.resolve(PositionProvider)
        touched = changed_lines(change.hunks, f.path)
        for node, pos in positions.items():
            if isinstance(node, cst.FunctionDef) and any(
                pos.start.line <= line <= pos.end.line for line in touched
            ):
                out.setdefault(node.name.value, wrapper.module.code_for_node(node))
    return out
