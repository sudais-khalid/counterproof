"""P3 mutant probe (PRD section 7.7): generation, validation, selection, execution, scoring.

M3 ships the rule-based half (RuleForge). LLM mutants, the LLM equivalence judge and
differential fuzzing join in M4 and plug into the same candidate list.

``test_only`` changes (section 8) have no source lines to mutate; instead the source lines
the changed tests execute are mutated and only the changed tests are run against them, so
new tests that kill nothing show up as UNPROVEN.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from counterproof.adapters.base import Adapter, CoverageMap
from counterproof.change.classify import is_test_path
from counterproof.config import Config
from counterproof.llm.roles import LLMSession
from counterproof.models import (
    BaselineReport,
    ChangeSet,
    Cost,
    Finding,
    Mutant,
    ProbeResult,
    Selection,
)
from counterproof.probes import lang
from counterproof.probes.integrity.probe import added_lines_by_file
from counterproof.probes.mutants.apply import invalid_reason
from counterproof.probes.mutants.equivalence import FuzzResult, judge_survivors
from counterproof.probes.mutants.llm_forge import forge, overfit_candidates
from counterproof.probes.mutants.rule_forge_py import Candidate
from counterproof.probes.mutants.runner import MutantJob, run_mutants
from counterproof.probes.mutants.select import select_mutants
from counterproof.probes.pyast import Literal
from counterproof.workspace.manager import Workspace

KILLED: Final = frozenset({"killed", "timeout"})
TEST_ONLY_MAX_LINES: Final = 200

# FR-MU-13 for rule mutants: a template hint per operator (LLM hints arrive in M4).
HINTS: Final[dict[str, str]] = {
    "boundary": "Add a test where the two sides of the comparison on line {line} are equal.",
    "relational": "Add a test that takes the other branch of the comparison on line {line}.",
    "arithmetic": "Add a test whose expected value depends on the exact arithmetic on line {line}.",
    "negation": "Add a test that exercises both outcomes of the condition on line {line}.",
    "constant": "Add a test that pins the constant on line {line}; a nearby value should fail.",
    "return_value": "Assert on the value returned on line {line}.",
    "statement_deletion": "Add a test that depends on the effect of line {line}.",
    "argument_swap": "Add a test that distinguishes the argument order on line {line}.",
    "exception_swallow": "Add a test that expects the error raised on line {line}.",
}


@dataclass
class MutantOutcome:
    result: ProbeResult
    mutants: list[Mutant] = field(default_factory=list)
    filtered: dict[str, int] = field(default_factory=dict)


def _scopes(
    change: ChangeSet,
    head_text: Callable[[str], str],
    coverage: CoverageMap | None,
    changed_tests: Sequence[str],
) -> dict[str, set[int]]:
    if change.intent == "test_only":
        wanted = set(changed_tests)
        lines: dict[str, set[int]] = {}
        for (path, line), tests in sorted((coverage or {}).items()):
            if lang.supported(path) and tests & wanted and not is_test_path(path):
                lines.setdefault(path, set()).add(line)
        flat = [(p, n) for p in sorted(lines) for n in sorted(lines[p])][:TEST_ONLY_MAX_LINES]
        capped: dict[str, set[int]] = {}
        for path, line in flat:
            capped.setdefault(path, set()).add(line)
        return capped
    added = added_lines_by_file(change)
    scopes: dict[str, set[int]] = {}
    for f in change.files:
        # FR-MU-01: source files only; tests are never mutated.
        if f.role != "source" or f.status == "deleted" or not lang.supported(f.path):
            continue
        changed = added.get(f.path, set())
        if changed:
            scopes[f.path] = lang.mutation_scope(f.path, head_text(f.path), changed)
    return scopes


@dataclass(frozen=True)
class LLMContext:
    """What the LLM half of P3 needs (absent when ``provider = none`` or no key)."""

    session: LLMSession
    mutant_model: str
    judge_model: str
    task_text: str | None
    sendable: Callable[[str], bool]
    test_source: Callable[[Sequence[str]], str]
    fuzz: Callable[[Mutant], FuzzResult] | None = None


def _to_mutant(mutant_id: str, candidate: Candidate) -> Mutant:
    return Mutant(
        id=mutant_id,
        source="llm" if candidate.source == "llm" else "rule",
        operator_or_class=candidate.operator,
        file=candidate.file,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        original=candidate.original,
        replacement=candidate.replacement,
        plausibility=candidate.plausibility,
        status="survived",  # provisional until it has been run
    )


def _template_hint(mutant: Mutant, line: int) -> str:
    template = HINTS.get(
        mutant.operator_or_class,
        "Add a test that distinguishes this {bug_class} fault on line {line}.",
    )
    return template.format(line=line, bug_class=mutant.operator_or_class)


def run_mutation_probe(
    *,
    change: ChangeSet,
    config: Config,
    adapter: Adapter,
    workers: Callable[[int], Sequence[Workspace]],
    head_text: Callable[[str], str],
    coverage: CoverageMap | None,
    selection: Selection,
    baseline: BaselineReport,
    changed_tests: Sequence[str],
    seed: bytes,
    junit_path: Callable[[str], Path],
    timeout_for: Callable[[Sequence[str]], float],
    test_literals: set[Literal] | None = None,
    llm: LLMContext | None = None,
) -> MutantOutcome:
    started = time.monotonic()
    stable = [t for t in baseline.stable if all(o == "pass" for o in baseline.outcomes.get(t, []))]
    stable_set = set(stable)
    test_only = change.intent == "test_only"
    changed_stable = [
        t for t in stable if any(t == c or t.startswith(c + "[") for c in changed_tests)
    ]

    scopes = _scopes(change, head_text, coverage, changed_tests)
    candidates: list[Candidate] = []
    for path, scope in sorted(scopes.items()):
        candidates += lang.rule_mutants(path, head_text(path), scope)

    filtered: dict[str, int] = {"candidates": len(candidates), "equivalent": 0, "invalid": 0}
    valid: list[Candidate] = []
    for candidate in candidates:
        if candidate.equivalent_reason:  # FR-MU-07 stage 1
            filtered["equivalent"] += 1
            continue
        text = head_text(candidate.file)
        lines = text.splitlines(keepends=True)
        mutated = (
            "".join(lines[: candidate.start_line - 1])
            + candidate.replacement
            + "".join(lines[candidate.end_line :])
        )
        if invalid_reason(text, mutated, candidate.file):  # FR-MU-06
            filtered["invalid"] += 1
            continue
        valid.append(candidate)

    required: list[Candidate] = []
    if llm is not None and scopes:
        # FR-MU-03/04: realistic faults plus the mandatory overfit mutant.
        changed = added_lines_by_file(change)
        forged = forge(
            llm.session,
            model=llm.mutant_model,
            scopes=scopes,
            changed=changed if not test_only else scopes,
            head_text=head_text,
            task_text=llm.task_text,
            test_literals=test_literals or set(),
            n=config.mutants.llm_budget,
            sendable=llm.sendable,
        )
        filtered["llm_proposed"] = forged.proposed
        filtered["llm_rejected"] = sum(forged.rejected.values())
        valid += forged.candidates
        required = overfit_candidates(forged.candidates)

    chosen = select_mutants(valid, config.mutants.budget, seed, required=required)  # FR-MU-05
    jobs: list[MutantJob] = []
    counters = {"rule": 0, "llm": 0}
    for candidate in chosen:
        kind = "llm" if candidate.source == "llm" else "rule"
        counters[kind] += 1
        mutant = _to_mutant(f"{kind[0]}{counters[kind]:03d}", candidate)
        if test_only:
            tests = changed_stable
        else:
            covering: set[str] = set()
            for line in range(mutant.start_line, mutant.end_line + 1):
                covering |= (coverage or {}).get((mutant.file, line), set())
            tests = sorted(covering & stable_set) or stable  # FR-MU-10 fallback
        mutant = mutant.model_copy(update={"covering_tests": list(tests)})
        jobs.append(MutantJob(mutant=mutant, tests=tests, timeout_s=timeout_for(tests)))

    pool = workers(min(config.run.workers, len(jobs))) if jobs else []
    executed = run_mutants(
        jobs, adapter=adapter, workers=pool, head_text=head_text, junit_path=junit_path
    )
    node_line = {job.mutant.id: c.line for job, c in zip(jobs, chosen, strict=True)}
    mutants = [
        m.model_copy(update={"hint": _template_hint(m, node_line[m.id])})
        if m.status == "survived"
        else m
        for m in executed
    ]
    if llm is not None:
        # FR-MU-08/09/11/13: judge survivors with their tests; hints from the judge.
        mutants = judge_survivors(
            mutants,
            session=llm.session,
            model=llm.judge_model,
            head_text=head_text,
            test_source=llm.test_source,
            fuzz=llm.fuzz if config.mutants.fuzz else None,
        )
    return MutantOutcome(
        result=_score(mutants, config, filtered, time.monotonic() - started),
        mutants=mutants,
        filtered=filtered,
    )


def _score(
    mutants: Sequence[Mutant], config: Config, filtered: Mapping[str, int], seconds: float
) -> ProbeResult:
    """FR-MU-12: kill ratio over non-equivalent, valid mutants (timeouts count as killed)."""
    killed = sum(1 for m in mutants if m.status in KILLED)
    survived = [m for m in mutants if m.status == "survived"]
    valid = killed + len(survived)
    excluded = [m for m in mutants if m.status in {"equivalent", "probably_equivalent"}]
    metrics: dict[str, float] = {
        "candidates": float(filtered.get("candidates", 0)),
        "filtered_equivalent": float(filtered.get("equivalent", 0)),
        "filtered_invalid": float(filtered.get("invalid", 0)),
        "selected": float(len(mutants)),
        "valid": float(valid),
        "killed": float(killed),
        "survived": float(len(survived)),
        "timeout": float(sum(1 for m in mutants if m.status == "timeout")),
        "invalid": float(sum(1 for m in mutants if m.status == "invalid")),
        "error": float(sum(1 for m in mutants if m.status == "error")),
        "high_plausibility_survivors": float(
            sum(1 for m in survived if m.source == "llm" and (m.plausibility or 0) >= 4)
        ),
        "equivalent": float(len(excluded)),
        "llm_proposed": float(filtered.get("llm_proposed", 0)),
        "llm_rejected": float(filtered.get("llm_rejected", 0)),
    }
    signals: list[str] = []
    if valid:
        ratio = killed / valid
        metrics["kill_ratio"] = round(ratio, 6)
        for source in ("rule", "llm"):  # FR-MU-12: reported separately
            group = [m for m in mutants if m.source == source and m.status in KILLED | {"survived"}]
            if group:
                metrics[f"kill_ratio_{source}"] = round(
                    sum(1 for m in group if m.status in KILLED) / len(group), 6
                )
        if ratio >= config.mutants.proven_at_least:
            signals.append("MU_STRONG")
        elif ratio >= config.mutants.unproven_below:
            signals.append("MU_PARTIAL")
        else:
            signals.append("MU_WEAK")
    else:
        signals.append("MU_NO_MUTANTS")
    if valid < 3:
        signals.append("MU_FEW_MUTANTS")
    if any(m.operator_or_class == "overfit" and m.source == "llm" for m in survived):
        signals.append("MU_OVERFIT_SURVIVED")  # FR-MU-04: the tests are too narrow
    findings = [
        Finding(
            rule_id="MU-SURVIVOR",
            severity="low",
            message=f"{m.operator_or_class} mutant {m.id} survived: "
            f"`{m.original.strip()}` -> `{m.replacement.strip()}`",
            file=m.file,
            line=m.start_line,
            base_snippet=m.original,
            head_snippet=m.replacement,
            evidence_refs=[f"mutants.json#{m.id}"],
        )
        for m in survived
    ]
    return ProbeResult(
        probe="mutants",
        status="ok" if valid else "inconclusive",
        signals=signals,
        findings=findings,
        metrics=metrics,
        cost=Cost(seconds=round(seconds, 6)),
    )
