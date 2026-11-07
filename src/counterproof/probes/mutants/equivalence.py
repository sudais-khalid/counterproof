"""Equivalence filter stages 2 and 3 for survivors (FR-MU-08, FR-MU-09, FR-MU-11, FR-MU-13).

Only survivors are judged: a killed mutant is already proven non-equivalent, so judging it
would only spend money. Each survivor is shown to the judge with comments and docstrings
stripped and with the tests that passed on it (the FR-MU-11 re-judge with test context).

Decision table (``fuzz`` is the stage-3 differential check, ``None`` when not applicable):

==================  ==============  =====================================================
judge               fuzz            result
==================  ==============  =====================================================
equivalent >= 0.8   differs         survived (a concrete input proves the judge wrong)
equivalent >= 0.8   same / None     equivalent (excluded from the kill ratio)
equivalent < 0.8    differs         survived
equivalent < 0.8    same            probably_equivalent (excluded)
equivalent < 0.8    None            survived
not equivalent      any             survived; its distinguishing input becomes the hint
no answer           any             survived (a skipped judge never hides a gap)
==================  ==============  =====================================================

A fuzz run that finds no difference never excludes a survivor the judge called
non-equivalent: weak random inputs must not hide a real gap.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Callable, Sequence
from typing import Final, Literal

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from counterproof.llm.roles import LLMSession
from counterproof.llm.schemas import JudgeOutput
from counterproof.models import Mutant
from counterproof.probes import lang
from counterproof.probes.mutants.apply import MutantMismatchError, apply_mutant

FuzzResult = Literal["differs", "same"] | None
THRESHOLD: Final = 0.8
MAX_TESTS_CHARS: Final = 6000
_TOKEN: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|\d+(?:\.\d+)?")


def enclosing_function(text: str, line: int) -> str:
    """Source of the innermost function containing ``line`` (the whole file if none)."""
    try:
        wrapper = MetadataWrapper(cst.parse_module(text), unsafe_skip_copy=True)
    except cst.ParserSyntaxError:
        return text
    positions = wrapper.resolve(PositionProvider)
    best: cst.FunctionDef | None = None
    for node, pos in positions.items():
        if (
            isinstance(node, cst.FunctionDef)
            and pos.start.line <= line <= pos.end.line
            and (best is None or pos.start.line >= positions[best].start.line)
        ):
            best = node
    return wrapper.module.code_for_node(best) if best is not None else text


def strip_comments_and_docstrings(source: str) -> str:
    """Normalised source without comments or docstrings (FR-MU-08 preprocessing)."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return source
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module)
            and isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def hint_mentions_mutation(hint: str, mutant: Mutant) -> bool:
    """FR-MU-13 check: the hint must refer to something on the mutated lines."""
    tokens = set(_TOKEN.findall(mutant.original)) | set(_TOKEN.findall(mutant.replacement))
    tokens -= {"return", "self", "None", "True", "False", "and", "not"}
    return any(token in hint for token in tokens)


def decide(
    judge: JudgeOutput | None, fuzz: FuzzResult
) -> Literal["survived", "equivalent", "probably_equivalent"]:
    if judge is None or not judge.equivalent:
        return "survived"
    if fuzz == "differs":
        return "survived"
    if judge.confidence >= THRESHOLD:
        return "equivalent"
    return "probably_equivalent" if fuzz == "same" else "survived"


def judge_survivors(
    mutants: Sequence[Mutant],
    *,
    session: LLMSession,
    model: str,
    head_text: Callable[[str], str],
    test_source: Callable[[Sequence[str]], str],
    fuzz: Callable[[Mutant], FuzzResult] | None = None,
) -> list[Mutant]:
    judged: list[Mutant] = []
    for mutant in mutants:
        if mutant.status != "survived":
            judged.append(mutant)
            continue
        text = head_text(mutant.file)
        path = mutant.file
        original = lang.strip_comments(path, lang.enclosing_function(path, text, mutant.start_line))
        try:
            mutated_text = apply_mutant(text, mutant)
        except MutantMismatchError:
            # Never crash a run: the survivor stays reported, un-judged.
            judged.append(
                mutant.model_copy(
                    update={"equivalence_note": "not judged: mutant did not re-apply"}
                )
            )
            continue
        changed = lang.strip_comments(
            path, lang.enclosing_function(path, mutated_text, mutant.start_line)
        )
        language = lang.language_name(path)
        verdict = session.run(
            "equivalence_judge",
            JudgeOutput,
            {
                "original": original,
                "mutant": changed,
                "tests": test_source(mutant.covering_tests)[:MAX_TESTS_CHARS]
                or ("// (none)" if language != "python" else "# (none)"),
            },
            model=model,
            max_tokens=800,
            variant=None if language == "python" else language,
        )
        fuzz_result = fuzz(mutant) if fuzz is not None else None
        status = decide(verdict, fuzz_result)
        notes = []
        if verdict is not None:
            notes.append(
                f"judge: {'equivalent' if verdict.equivalent else 'differs'} "
                f"({verdict.confidence:.2f})"
            )
        else:
            notes.append("judge: no answer")
        if fuzz_result is not None:
            notes.append(f"fuzz: {fuzz_result}")
        update: dict[str, object] = {"status": status, "equivalence_note": "; ".join(notes)}
        if status != "survived":
            update["hint"] = None
        elif (
            verdict is not None
            and not verdict.equivalent
            and verdict.distinguishing_input
            and hint_mentions_mutation(verdict.distinguishing_input, mutant)
        ):
            update["hint"] = verdict.distinguishing_input.strip()[:400]
        judged.append(mutant.model_copy(update=update))
    return judged
