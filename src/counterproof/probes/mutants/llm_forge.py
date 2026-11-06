"""LLMForge (FR-MU-03) and the mandatory overfit mutant (FR-MU-04).

One ``mutant_forge`` call per change: the changed source lines with a 20-line context window
(FR-CR-02), the task text and the literals the tests use. Every proposed mutant is validated
here before it can be selected: it must stay inside the changed lines (or directly next to
them), be minimal, never touch a test file, and parse and compile (FR-MU-06).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from counterproof.llm.roles import LLMSession
from counterproof.llm.schemas import ForgeMutant, ForgeOutput
from counterproof.probes.mutants.apply import invalid_reason
from counterproof.probes.mutants.rule_forge_py import Candidate
from counterproof.probes.pyast import Literal, is_trivial

BUG_CLASSES: Final = (
    "off-by-one, wrong boundary, missing case, wrong branch order, wrong default, partial fix "
    "that only handles the reported example, silently swallowed error, wrong unit or rounding, "
    "state not reset, inverted condition"
)
CONTEXT_LINES: Final = 20
MAX_MUTANT_LINES: Final = 5
MAX_LITERALS: Final = 40
OVERFIT: Final = "overfit"
SPECIAL_CASE: Final = "special case (overfit with fallback)"


@dataclass
class ForgeResult:
    candidates: list[Candidate] = field(default_factory=list)
    proposed: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    called: bool = False


def _fmt_literal(lit: Literal) -> str:
    kind, value = lit
    return repr(value) if kind == "str" else value


def literal_list(literals: set[Literal]) -> str:
    shown = sorted((_fmt_literal(lit) for lit in literals if not is_trivial(lit)), key=str)
    return ", ".join(shown[:MAX_LITERALS]) or "(none)"


def render_files(
    scopes: Mapping[str, set[int]], changed: Mapping[str, set[int]], head_text: Callable[[str], str]
) -> str:
    """Numbered source excerpts; ``>`` marks changed lines."""
    blocks = []
    for path in sorted(scopes):
        lines = head_text(path).splitlines()
        scope = scopes[path]
        first = max(1, min(scope) - CONTEXT_LINES)
        last = min(len(lines), max(scope) + CONTEXT_LINES)
        marked = changed.get(path, set())
        body = "\n".join(
            f"{'>' if n in marked else ' '} {n:4d} | {lines[n - 1]}" for n in range(first, last + 1)
        )
        blocks.append(f"### {path}\n{body}")
    return "\n\n".join(blocks)


def keeps_original_logic(original: str, replacement: str) -> bool:
    """Every non-blank original line still appears, in order, in the replacement."""
    wanted = [line.strip() for line in original.splitlines() if line.strip()]
    have = iter(line.strip() for line in replacement.splitlines())
    return bool(wanted) and all(any(line == got for got in have) for line in wanted)


def _validate(
    m: ForgeMutant, scopes: Mapping[str, set[int]], head_text: Callable[[str], str]
) -> tuple[Candidate | None, str]:
    if m.file not in scopes:
        return None, "file not among the changed source files"
    text = head_text(m.file)
    lines = text.splitlines(keepends=True)
    if not (1 <= m.start_line <= m.end_line <= len(lines)):
        return None, "line range outside the file"
    if m.end_line - m.start_line + 1 > MAX_MUTANT_LINES:
        return None, "not minimal (more than 5 lines)"
    allowed = {n + d for n in scopes[m.file] for d in (-1, 0, 1)}
    if not allowed & set(range(m.start_line, m.end_line + 1)):
        return None, "outside the changed lines"
    replacement = m.replacement if m.replacement.endswith("\n") else m.replacement + "\n"
    original = "".join(lines[m.start_line - 1 : m.end_line])
    if replacement == original:
        return None, "identical to the original"
    mutated = "".join(lines[: m.start_line - 1]) + replacement + "".join(lines[m.end_line :])
    reason = invalid_reason(text, mutated, m.file)
    if reason:
        return None, reason
    bug_class = OVERFIT if OVERFIT in m.bug_class.lower() else m.bug_class.strip()[:60]
    if bug_class == OVERFIT and keeps_original_logic(original, replacement):
        # FR-MU-04: an overfit mutant *replaces* the general logic. "Special case + the
        # original code as fallback" is not one, whatever the model calls it; it still runs
        # as an ordinary mutant but cannot raise MU_OVERFIT_SURVIVED.
        bug_class = SPECIAL_CASE
    return (
        Candidate(
            operator=bug_class or "llm",
            file=m.file,
            line=m.start_line,
            start_line=m.start_line,
            end_line=m.end_line,
            original=original,
            replacement=replacement,
            source="llm",
            plausibility=m.plausibility,
            rationale=m.rationale[:300],
        ),
        "",
    )


def forge(
    session: LLMSession,
    *,
    model: str,
    scopes: Mapping[str, set[int]],
    changed: Mapping[str, set[int]],
    head_text: Callable[[str], str],
    task_text: str | None,
    test_literals: set[Literal],
    n: int,
    sendable: Callable[[str], bool],
) -> ForgeResult:
    """Ask the model for up to ``n`` realistic faults plus one overfit mutant."""
    allowed = {path: lines for path, lines in scopes.items() if sendable(path)}
    result = ForgeResult()
    if not allowed or n <= 0:
        return result
    variables = {
        "n": str(n),
        "bug_classes": BUG_CLASSES,
        "test_literals": literal_list(test_literals),
        "task": (task_text or "(no task description)").strip()[:4000],
        "files": render_files(allowed, changed, head_text),
    }
    result.called = True
    output = session.run("mutant_forge", ForgeOutput, variables, model=model, max_tokens=4096)
    if output is None:
        return result
    seen: set[tuple[str, int, int, str]] = set()
    for proposed in output.mutants[: n + 1]:
        result.proposed += 1
        candidate, why = _validate(proposed, allowed, head_text)
        key = (proposed.file, proposed.start_line, proposed.end_line, proposed.replacement)
        if candidate is None or key in seen:
            reason = why or "duplicate"
            result.rejected[reason] = result.rejected.get(reason, 0) + 1
            continue
        seen.add(key)
        result.candidates.append(candidate)
    return result


def overfit_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    return [c for c in candidates if c.source == "llm" and c.operator == OVERFIT][:1]
