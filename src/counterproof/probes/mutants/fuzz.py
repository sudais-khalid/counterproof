"""Equivalence filter stage 3: differential fuzzing with Hypothesis (FR-MU-09, optional).

Applies to pure top-level functions whose parameters are all annotated with simple types
(int, float, str, bool, Decimal). The original module and an in-memory copy with the mutant
applied are called with the same 200 generated inputs (seeded from the test literals,
derandomised so runs are reproducible); any different return value or exception type proves
the mutant non-equivalent. Runs in the project's interpreter, inside the head worktree, and
only if Hypothesis is installed there.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

from counterproof.models import Mutant
from counterproof.probes.mutants.apply import MutantMismatchError, apply_mutant
from counterproof.probes.mutants.equivalence import FuzzResult
from counterproof.probes.pyast import Literal, module_name

SIMPLE_TYPES: Final = frozenset({"int", "float", "str", "bool", "Decimal", "decimal.Decimal"})
MAX_EXAMPLES: Final = 200

SCRIPT: Final = r"""
import importlib, importlib.util, json, sys
from decimal import Decimal
SPEC = json.loads(open(sys.argv[1], encoding="utf-8").read())
try:
    from hypothesis import HealthCheck, given, settings, strategies as st
except ImportError:
    print("unavailable"); raise SystemExit(0)
mod = importlib.import_module(SPEC["module"])
spec = importlib.util.spec_from_loader(SPEC["module"] + "_cp_mutant", loader=None)
mutant = importlib.util.module_from_spec(spec)
mutant.__package__ = mod.__package__
exec(compile(SPEC["source"], mod.__file__, "exec"), mutant.__dict__)
f, g = getattr(mod, SPEC["function"]), getattr(mutant, SPEC["function"])
lits = SPEC["literals"]

def strategy(kind):
    nums = [x for x in lits if isinstance(x, (int, float))]
    if kind == "int":
        seeds = sorted({int(x) for x in nums} | {0, 1, -1})
        return st.one_of(st.sampled_from(seeds), st.integers(-10**6, 10**6))
    if kind == "float":
        seeds = sorted({float(x) for x in nums} | {0.0, 1.0, -1.0})
        return st.one_of(st.sampled_from(seeds), st.floats(-1e6, 1e6, allow_nan=False))
    if kind in ("Decimal", "decimal.Decimal"):
        edges = {Decimal("0"), Decimal("0.005"), Decimal("-0.005")}
        seeds = sorted({Decimal(str(x)) for x in nums} | edges)
        generic = st.decimals(-1000, 1000, places=3, allow_nan=False, allow_infinity=False)
        return st.one_of(st.sampled_from(seeds), generic)
    if kind == "bool":
        return st.booleans()
    strs = [x for x in lits if isinstance(x, str)] or [""]
    return st.one_of(st.sampled_from(strs), st.text(max_size=20))

def outcome(fn, args):
    try:
        return ("ok", repr(fn(*args)))
    except Exception as exc:
        return ("raise", type(exc).__name__)

@settings(max_examples=SPEC["examples"], derandomize=True, database=None, deadline=None,
          suppress_health_check=list(HealthCheck))
@given(st.tuples(*[strategy(k) for k in SPEC["types"]]))
def check(args):
    assert outcome(f, args) == outcome(g, args)

try:
    check()
    print("same")
except AssertionError:
    print("differs")
"""


def fuzz_target(text: str, line: int) -> tuple[str, list[str]] | None:
    """``(function_name, parameter_types)`` if the mutated line is in a fuzzable function."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:  # top-level functions only: no methods, no closures
        if not isinstance(node, ast.FunctionDef):
            continue
        if not (node.lineno <= line <= (node.end_lineno or node.lineno)):
            continue
        args = node.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs or not args.args:
            return None
        types = []
        for arg in args.args:
            if arg.annotation is None:
                return None
            name = ast.unparse(arg.annotation).replace(" ", "")
            if name not in SIMPLE_TYPES:
                return None
            types.append(name)
        return node.name, types
    return None


def _literal_values(literals: set[Literal]) -> list[object]:
    values: list[object] = []
    for kind, value in sorted(literals):
        if kind == "num":
            values.append(float(value) if "." in value else int(value))
        elif kind == "str" and len(value) <= 40:
            values.append(value)
    return values[:60]


def make_fuzzer(
    *,
    head_text: Callable[[str], str],
    literals: set[Literal],
    run_script: Callable[[Path, list[str]], str | None],
    script_dir: Path,
) -> Callable[[Mutant], FuzzResult]:
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "fuzz_equivalence.py"
    script.write_text(SCRIPT, encoding="utf-8")
    lits = _literal_values(literals)

    def fuzz(mutant: Mutant) -> FuzzResult:
        text = head_text(mutant.file)
        target = fuzz_target(text, mutant.start_line)
        if target is None or not mutant.file.endswith(".py"):
            return None
        try:
            mutated = apply_mutant(text, mutant)
        except MutantMismatchError:
            return None  # never crash a run over an optional equivalence check
        spec = {
            "module": module_name(mutant.file),
            "function": target[0],
            "types": target[1],
            "source": mutated,
            "literals": lits,
            "examples": MAX_EXAMPLES,
        }
        spec_path = script_dir / f"fuzz-{mutant.id}.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        out = run_script(script, [str(spec_path)])
        verdict = (out or "").strip().splitlines()[-1:] or [""]
        return verdict[0] if verdict[0] in {"differs", "same"} else None  # type: ignore[return-value]

    return fuzz
