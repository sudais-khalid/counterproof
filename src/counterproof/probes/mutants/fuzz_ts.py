"""Equivalence filter stage 3 for TypeScript/JavaScript: differential fuzzing with fast-check
(FR-MU-09, optional).

For an exported top-level function whose parameters have simple types (``number``,
``string``, ``boolean``, arrays of those, local aliases of them, optional or defaulted), a
mutant copy of the module is written next to the original (so relative imports resolve) and
a generated Vitest file calls both with the same 200 seeded inputs, seeded with the test
literals. Any different return value or error type proves the mutant non-equivalent. Runs in
the head worktree with the project's own Vitest/Jest and only if fast-check is installed
there; both generated files are removed afterwards.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Final

from tree_sitter import Node

from counterproof.models import Mutant
from counterproof.probes.mutants.apply import MutantMismatchError, apply_mutant
from counterproof.probes.mutants.equivalence import FuzzResult
from counterproof.probes.pyast import Literal
from counterproof.probes.tsast import parse, text_of

MAX_EXAMPLES: Final = 200
MARKER: Final = "CP_FUZZ_RESULT:"
_SIMPLE: Final = {"number": "num", "string": "str", "boolean": "bool"}
_ALIAS: Final = re.compile(r"\btype\s+(\w+)\s*=\s*(number|string|boolean)\s*;")

SCRIPT: Final = """import { test } from "%(runner)s";
import fc from "fast-check";
import * as original from "./%(module)s";
import * as mutant from "./%(mutant)s";

const SPEC = %(spec)s;

function arb(kind: string): fc.Arbitrary<unknown> {
  const nums = SPEC.literals.filter((x: unknown) => typeof x === "number");
  const strs = SPEC.literals.filter((x: unknown) => typeof x === "string");
  const base = kind.replace(/\\[\\]$/, "").replace(/\\?$/, "");
  let a: fc.Arbitrary<unknown>;
  if (base === "num") {
    a = fc.oneof(fc.constantFrom(...nums, 0, 1, -1), fc.integer({ min: -100000, max: 100000 }));
  } else if (base === "bool") {
    a = fc.boolean();
  } else {
    a = fc.oneof(fc.constantFrom(...strs, ""), fc.string({ maxLength: 20 }));
  }
  if (kind.includes("[]")) a = fc.array(a, { maxLength: 8 });
  if (kind.endsWith("?")) a = fc.option(a, { nil: undefined });
  return a;
}

function outcome(fn: (...args: unknown[]) => unknown, args: unknown[]): string {
  try {
    return JSON.stringify({ ok: fn(...args) }) ?? "undefined";
  } catch (error) {
    return JSON.stringify({ raise: (error as Error)?.constructor?.name ?? "error" });
  }
}

test("counterproof equivalence fuzz", () => {
  const f = (original as Record<string, never>)[SPEC.function];
  const g = (mutant as Record<string, never>)[SPEC.function];
  let verdict = "same";
  try {
    fc.assert(
      fc.property(fc.tuple(...SPEC.types.map(arb)), (args) =>
        outcome(f, args) === outcome(g, args)),
      { numRuns: SPEC.examples, seed: SPEC.seed },
    );
  } catch {
    verdict = "differs";
  }
  console.log("%(marker)s" + verdict);
});
"""


def _aliases(text: str) -> dict[str, str]:
    return dict(_ALIAS.findall(text))


def _param_kind(param: Node, aliases: dict[str, str]) -> str | None:
    annotation = param.child_by_field_name("type")
    if annotation is None:
        return None
    raw = text_of(annotation).lstrip(":").strip().removeprefix("readonly ").strip()
    array = raw.endswith("[]")
    name = raw.removesuffix("[]").strip()
    name = aliases.get(name, name)
    kind = _SIMPLE.get(name)
    if kind is None:
        return None
    kind += "[]" if array else ""
    optional = param.type == "optional_parameter" or param.child_by_field_name("value") is not None
    return kind + ("?" if optional else "")


def fuzz_target(
    path: str, text: str, line: int, aliases: dict[str, str]
) -> tuple[str, list[str]] | None:
    """``(function, parameter kinds)`` for an exported top-level function around ``line``."""
    tree = parse(path, text)
    if tree is None:
        return None
    for stmt in tree.root_node.named_children:
        if stmt.type != "export_statement":
            continue
        decl = stmt.child_by_field_name("declaration")
        if decl is None or decl.type != "function_declaration":
            continue
        if not (decl.start_point.row + 1 <= line <= decl.end_point.row + 1):
            continue
        params = decl.child_by_field_name("parameters")
        kinds = []
        for param in params.named_children if params is not None else []:
            kind = _param_kind(param, aliases)
            if kind is None:
                return None
            kinds.append(kind)
        name = decl.child_by_field_name("name")
        return (text_of(name), kinds) if name is not None and kinds else None
    return None


def _literal_values(literals: set[Literal]) -> list[object]:
    values: list[object] = []
    for kind, value in sorted(literals):
        if kind == "num":
            values.append(float(value) if "." in value else int(value))
        elif kind == "str" and len(value) <= 40:
            values.append(value)
    return values[:60]


def make_ts_fuzzer(
    *,
    head_text: Callable[[str], str],
    literals: set[Literal],
    ws_root: Path,
    runner: str,
    run_script: Callable[[Path], str | None],
) -> Callable[[Mutant], FuzzResult]:
    """``runner`` is ``vitest`` or ``jest`` (for the ``test`` import); fast-check must be
    installed in the worktree's ``node_modules``."""
    available = (ws_root / "node_modules" / "fast-check").is_dir()
    lits = _literal_values(literals)

    def fuzz(mutant: Mutant) -> FuzzResult:
        if not available:
            return None
        text = head_text(mutant.file)
        aliases: dict[str, str] = {}
        folder = PurePosixPath(mutant.file).parent
        for sibling in (ws_root / folder).glob("*.ts"):
            aliases |= _aliases(sibling.read_text(encoding="utf-8", errors="replace"))
        target = fuzz_target(mutant.file, text, mutant.start_line, aliases)
        if target is None:
            return None
        try:
            mutated = apply_mutant(text, mutant)
        except MutantMismatchError:
            return None
        source = ws_root / mutant.file
        stem = source.name.split(".", 1)[0]
        mutant_file = source.with_name(f"{stem}.cpmutant{source.suffix}")
        script = source.with_name(f"{stem}.cpfuzz.test{source.suffix}")
        spec = {
            "function": target[0],
            "types": target[1],
            "literals": lits,
            "examples": MAX_EXAMPLES,
            "seed": sum(ord(c) for c in mutant.id + mutant.file) % 100_000,
        }
        mutant_file.write_text(mutated, encoding="utf-8")
        script.write_text(
            SCRIPT
            % {
                "runner": "vitest" if runner == "vitest" else "@jest/globals",
                "module": stem,
                "mutant": f"{stem}.cpmutant",
                "spec": json.dumps(spec),
                "marker": MARKER,
            },
            encoding="utf-8",
        )
        try:
            out = run_script(script) or ""
        finally:
            mutant_file.unlink(missing_ok=True)
            script.unlink(missing_ok=True)
        found = re.search(re.escape(MARKER) + r"(same|differs)", out)
        return found.group(1) if found else None  # type: ignore[return-value]

    return fuzz
