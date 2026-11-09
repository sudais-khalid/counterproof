"""OracleWriter (FR-BO-02): blind tests from the task and the interface, validated.

The model's tests are LLM-written code that will run locally, so every test is checked
before it is written: valid Python, exactly one top-level ``test_*`` function, imports of
public names only (first-party names must be in the interface the oracle saw), and none of
the modules or builtins that reach outside the test (files, processes, network).
"""

from __future__ import annotations

import ast
import builtins
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from counterproof.llm.roles import LLMSession
from counterproof.llm.schemas import OracleOutput, OracleTest
from counterproof.probes.blind_oracle.interface import Interface

ORACLE_FILENAME: Final = "test_counterproof_oracle.py"
MAX_TASK_CHARS: Final = 4000
_BANNED_MODULES: Final = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "ctypes",
        "urllib",
        "http",
        "requests",
        "httpx",
        "pathlib",
        "tempfile",
        "glob",
        "importlib",
        "multiprocessing",
        "threading",
        "signal",
        "pickle",
        "marshal",
        "builtins",
        "asyncio",
        "time",
        "random",
    }
)
_BANNED_CALLS: Final = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "vars",
        "setattr",
        "delattr",
        "getattr",
    }
)
_INTENT: Final = re.compile(r"^\s*#\s*intent:.*$", re.M | re.I)
_NAME: Final = re.compile(r"[^a-z0-9_]+")
_BUILTINS: Final = frozenset(dir(builtins))


@dataclass(frozen=True)
class OracleCase:
    name: str
    intent: str
    code: str
    model: str = ""


@dataclass(frozen=True)
class OracleSuite:
    imports: list[str] = field(default_factory=list)
    cases: list[OracleCase] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    """``(test name, reason)`` for tests that failed validation."""
    language: str = "python"

    def text(self, header: str, names: Sequence[str] | None = None) -> str:
        chosen = [c for c in self.cases if names is None or c.name in names]
        imports = "\n".join(self.imports)
        if self.language == "python":
            body = "\n\n\n".join(c.code.rstrip() for c in chosen)
            return f'"""{header}"""\n\n{imports}\n\n\n{body}\n'
        body = "\n\n".join(c.code.rstrip() for c in chosen)
        return f"/** {header} */\n\n{imports}\n\n{body}\n"


def oracle_filename(interface: Interface) -> str:
    """``test_counterproof_oracle.py`` or ``counterproof_oracle.test.ts`` (or ``.js``, ...)."""
    if interface.language == "python":
        return ORACLE_FILENAME
    return f"counterproof_oracle{interface.file_suffix}"


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _check_import(node: ast.Import | ast.ImportFrom, interface: Interface) -> str | None:
    first_party = {_root(m) for m in interface.public_names}
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return "relative import"
        module = node.module or ""
        if _root(module) in _BANNED_MODULES:
            return f"imports {module}"
        if any(part.startswith("_") for part in module.split(".")):
            return f"imports private module {module}"
        for alias in node.names:
            if alias.name == "*":
                return "star import"
            if alias.name.startswith("_"):
                return f"imports private name {alias.name}"
        if _root(module) in first_party:
            exposed = interface.public_names.get(module)
            if exposed is None:
                return f"imports {module}, which is not in the interface"
            missing = [a.name for a in node.names if a.name not in exposed]
            if missing:
                return f"imports {', '.join(missing)} from {module}: not in the interface"
        return None
    for alias in node.names:
        if _root(alias.name) in _BANNED_MODULES:
            return f"imports {alias.name}"
        if any(part.startswith("_") for part in alias.name.split(".")):
            return f"imports private module {alias.name}"
    return None


def _check_tree(tree: ast.AST, interface: Interface) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            problem = _check_import(node, interface)
            if problem:
                return problem
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BANNED_CALLS:
                return f"calls {node.func.id}()"
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("_") and not (attr.startswith("__") and attr.endswith("__")):
                return f"uses private attribute {attr}"
    return None


def _bound_by_imports(nodes: Iterable[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".", 1)[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names}
    return names


def _undefined(tree: ast.Module, available: set[str]) -> list[str]:
    """Names a test reads that nothing binds (they would only raise NameError)."""
    bound = set(available) | _BUILTINS | _bound_by_imports(ast.walk(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef) or (
            isinstance(node, ast.ExceptHandler) and node.name
        ):
            bound.add(node.name)  # type: ignore[arg-type]
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(used - bound)


def _slug(text: str) -> str:
    return _NAME.sub("_", text.lower()).strip("_")[:60] or "case"


def validate(output: OracleOutput, interface: Interface, *, k: int, model: str = "") -> OracleSuite:
    imports: list[str] = []
    rejected: list[tuple[str, str]] = []
    for line in output.imports:
        stmt = line.strip()
        try:
            tree = ast.parse(stmt)
        except SyntaxError:
            rejected.append(("<imports>", f"invalid import {stmt!r}"))
            continue
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Import | ast.ImportFrom):
            rejected.append(("<imports>", f"not a single import: {stmt!r}"))
            continue
        problem = _check_import(tree.body[0], interface)
        if problem:
            rejected.append(("<imports>", problem))
            continue
        if stmt not in imports:
            imports.append(stmt)

    cases: list[OracleCase] = []
    seen: set[str] = set()
    available = _bound_by_imports([ast.parse(i).body[0] for i in imports])
    for test in output.tests:
        case, problem = _case(test, interface, seen, model, available)
        if case is None:
            rejected.append((test.name, problem or "invalid"))
            continue
        seen.add(case.name)
        cases.append(case)
        if len(cases) == k:
            break
    suite = OracleSuite(imports=imports, cases=cases, rejected=rejected)
    try:  # the assembled module must parse too
        ast.parse(suite.text("check"))
    except SyntaxError as exc:
        return OracleSuite(imports=[], cases=[], rejected=[*rejected, ("<file>", str(exc))])
    return suite


def _case(
    test: OracleTest, interface: Interface, seen: set[str], model: str, available: set[str]
) -> tuple[OracleCase | None, str | None]:
    code = _INTENT.sub("", test.code).strip("\n")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return None, f"syntax error: {exc.msg}"
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    others = [
        n for n in tree.body if not isinstance(n, ast.FunctionDef | ast.Import | ast.ImportFrom)
    ]
    if len(functions) != 1 or others or not functions[0].name.startswith("test"):
        return None, "must be exactly one top-level test function"
    problem = _check_tree(tree, interface)
    if problem:
        return None, problem
    undefined = _undefined(tree, available)
    if undefined:
        return None, f"uses undefined name(s) {', '.join(undefined)}"
    original = functions[0].name
    name = f"test_oracle_{_slug(original.removeprefix('test_').removeprefix('test'))}"
    while name in seen:
        name += "_2"
    code = re.sub(rf"\bdef\s+{re.escape(original)}\s*\(", f"def {name}(", code, count=1)
    intent = " ".join(test.intent.split())[:200]
    return OracleCase(
        name=name, intent=intent, code=f"# intent: {intent}\n{code}\n", model=model
    ), None


def write_oracle_tests(
    session: LLMSession,
    *,
    models: Sequence[str],
    task_text: str | None,
    interface: Interface,
    k: int,
) -> OracleSuite:
    """One writer call per oracle model (FR-BO-02, FR-BO-07); suites are merged."""
    typescript = interface.language != "python"
    variables = {
        "task": (task_text or "(no task text given; test only what the docstrings state)")[
            :MAX_TASK_CHARS
        ],
        "interface": interface.render(),
        "style": interface.style_example
        or ("// (no example test file)" if typescript else "# (no example test file)"),
        "k": str(k),
    }
    if typescript:
        variables["framework"] = interface.framework
    imports: list[str] = []
    cases: list[OracleCase] = []
    rejected: list[tuple[str, str]] = []
    for model in models:
        output = session.run(
            "oracle_writer",
            OracleOutput,
            variables,
            model=model,
            max_tokens=4000,
            variant="typescript" if typescript else None,
        )
        if output is None:
            continue
        if typescript:
            from counterproof.probes.blind_oracle.writer_ts import validate_ts  # noqa: PLC0415

            suite = validate_ts(output, interface, k=k, framework=interface.framework, model=model)
        else:
            suite = validate(output, interface, k=k, model=model)
        taken = {c.name for c in cases}
        for found in suite.cases:
            name = found.name
            while name in taken:
                name += "_2"
            code = found.code.replace(f"def {found.name}(", f"def {name}(", 1)
            taken.add(name)
            cases.append(OracleCase(name=name, intent=found.intent, code=code, model=found.model))
        imports += [i for i in suite.imports if i not in imports]
        rejected += suite.rejected
    return OracleSuite(imports=imports, cases=cases, rejected=rejected, language=interface.language)
