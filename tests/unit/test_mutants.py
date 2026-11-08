"""P3 rule mutants: FR-MU-01 scope, FR-MU-02 operators, FR-MU-05 selection, FR-MU-06 validity,
FR-MU-07 static equivalence, FR-MU-10/11 execution and FR-MU-12 scoring."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from counterproof.config import build_config
from counterproof.models import FailureInfo, Mutant, RunResult, TestOutcome
from counterproof.probes.mutants.apply import MutantMismatchError, apply_mutant, invalid_reason
from counterproof.probes.mutants.probe import _score
from counterproof.probes.mutants.rule_forge_py import Candidate, folds_to_same_value, generate
from counterproof.probes.mutants.runner import MutantJob, classify_run, run_mutants
from counterproof.probes.mutants.scope import mutation_scope
from counterproof.probes.mutants.select import select_mutants
from counterproof.workspace.manager import Workspace
from tests.helpers import git

SOURCE = dedent(
    '''\
    """Pricing."""

    LIMIT = 100


    def price(total, cap=None, *, strict=False):
        """Docstring is never mutated."""
        if total < 0:
            raise ValueError("negative")
        discount = total * 10 // 100
        if cap is not None and discount > cap:
            discount = cap
        discount += 1
        label = "SALE"
        log(total, cap)
        return total - discount
    '''
)


def _mutants(lines: set[int]) -> list[Candidate]:
    return generate("src/pricing.py", SOURCE, lines)


def _by_operator(lines: set[int]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for c in _mutants(lines):
        result.setdefault(c.operator, []).append(c.replacement.strip())
    return result


def test_fr_mu_01_scope_adds_enclosing_condition_and_following_return() -> None:
    # Changing line 12 (inside `if` on line 11) pulls in the condition and the return.
    assert mutation_scope(SOURCE, {12}) == {11, 12, 16}


def test_fr_mu_01_scope_never_exceeds_two_context_lines_per_block() -> None:
    assert len(mutation_scope(SOURCE, {12}) - {12}) <= 2
    assert mutation_scope(SOURCE, set()) == set()


def test_fr_mu_02_relational_and_boundary() -> None:
    ops = _by_operator({8})
    assert "if total <= 0:" in ops["boundary"]
    assert "if total > 0:" in ops["relational"]
    assert "if not (total < 0):" in ops["negation"]
    assert "if total < 1:" in ops["constant"]


def test_fr_mu_02_arithmetic_and_constants() -> None:
    ops = _by_operator({10})
    assert "discount = total / 10 // 100" in ops["arithmetic"]
    assert "discount = total * 10 / 100" in ops["arithmetic"]
    assert "discount = total * 11 // 100" in ops["constant"]
    assert "pass" in ops["statement_deletion"]


def test_fr_mu_02_boolean_negation_and_is() -> None:
    ops = _by_operator({11})
    assert "if cap is not None or discount > cap:" in ops["negation"]
    assert "if cap is None and discount > cap:" in ops["relational"]
    assert "if cap is not None and discount >= cap:" in ops["boundary"]


def test_fr_mu_02_exception_swallow_return_value_string_and_swap() -> None:
    assert _by_operator({9})["exception_swallow"] == ["pass"]
    assert _by_operator({16})["return_value"] == ["return None"]
    assert 'label = ""' in _by_operator({14})["constant"]
    assert "log(cap, total)" in _by_operator({15})["argument_swap"]
    assert "discount -= 1" in _by_operator({13})["arithmetic"]


def test_fr_mu_02_numeric_strings_are_perturbed_not_emptied() -> None:
    mutants = generate("a.py", 'STEP = Decimal("0.05")\n', {1})
    constants = [c.replacement for c in mutants if c.operator == "constant"]
    assert constants == ['STEP = Decimal("0.06")\n']


def test_fr_mu_02_docstrings_and_unscoped_lines_are_never_mutated() -> None:
    assert _mutants({7}) == []
    lines = {c.line for c in _mutants({8, 9})}
    assert lines <= {8, 9}


def test_mutants_are_minimal_line_ranges() -> None:
    for c in _mutants(set(range(1, 17))):
        assert c.start_line == c.end_line == c.line
        assert c.original == SOURCE.splitlines(keepends=True)[c.line - 1]


def test_fr_mu_07_constant_folding_and_unreachable_code() -> None:
    assert folds_to_same_value("2 * 2", "2 + 2")
    assert not folds_to_same_value("2 * 1", "2 / 1")  # 2 vs 2.0: observably different
    assert not folds_to_same_value("x * 2", "x + 2")
    folded = generate("a.py", "X = 3 + 0\n", {1})
    assert [c.equivalent_reason for c in folded if c.operator == "arithmetic"] == [
        "constant-folds to the same value"
    ]
    dead = generate("a.py", "def f(a):\n    return a\n    a = a + 1\n", {3})
    assert dead and all(c.equivalent_reason and "unreachable" in c.equivalent_reason for c in dead)


def _mutant(**kw: Any) -> Mutant:
    base: dict[str, Any] = {
        "id": "r001",
        "source": "rule",
        "operator_or_class": "boundary",
        "file": "src/pricing.py",
        "start_line": 8,
        "end_line": 8,
        "original": "    if total < 0:\n",
        "replacement": "    if total <= 0:\n",
        "status": "survived",
    }
    base.update(kw)
    return Mutant.model_validate(base)


def test_fr_mu_06_apply_and_round_trip() -> None:
    mutated = apply_mutant(SOURCE, _mutant())
    assert "if total <= 0:" in mutated
    assert mutated.count("\n") == SOURCE.count("\n")
    with pytest.raises(MutantMismatchError):
        apply_mutant(mutated, _mutant())


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ("    if total < 0 :  # same tree\n", "syntax tree unchanged"),
        ("    if total <:\n", "does not parse"),
        ("    if total <= 0:\n", None),
    ],
)
def test_fr_mu_06_validation(replacement: str, reason: str | None) -> None:
    mutated = apply_mutant(SOURCE, _mutant(replacement=replacement))
    assert invalid_reason(SOURCE, mutated, "src/pricing.py") == reason


def test_fr_mu_06_parses_but_does_not_compile() -> None:
    mutant = _mutant(
        start_line=14,
        end_line=14,
        original='    label = "SALE"\n',
        replacement="    nonlocal label\n",
    )
    mutated = apply_mutant(SOURCE, mutant)
    assert invalid_reason(SOURCE, mutated, "src/pricing.py") == "does not compile"


def _candidates() -> list[Candidate]:
    return [c for c in _mutants(set(range(8, 17))) if c.equivalent_reason is None]


def test_fr_mu_05_budget_and_at_most_two_per_line() -> None:
    chosen = select_mutants(_candidates(), 6, b"\x01" * 16)
    assert len(chosen) == 6
    per_line: dict[int, int] = {}
    for c in chosen:
        per_line[c.line] = per_line.get(c.line, 0) + 1
    assert max(per_line.values()) <= 2


def test_fr_mu_05_seeded_selection_is_deterministic_and_seed_dependent() -> None:
    pool = _candidates()
    a = select_mutants(pool, 5, b"\x01" * 16)
    assert a == select_mutants(list(reversed(pool)), 5, b"\x01" * 16)
    others = {
        tuple(c.replacement for c in select_mutants(pool, 5, bytes([i]) * 16)) for i in range(20)
    }
    assert len(others) > 1, "different seeds should be able to pick different mutants"


def test_fr_mu_05_higher_prior_operators_first() -> None:
    chosen = select_mutants(_mutants({8}), 1, b"\x00" * 16)
    assert [c.operator for c in chosen] == ["boundary"]


def _result(outcomes: dict[str, TestOutcome], kinds: dict[str, str] | None = None) -> RunResult:
    return RunResult(
        tests=outcomes,
        failures={t: FailureInfo(kind=k) for t, k in (kinds or {}).items()},  # type: ignore[arg-type]
        duration_s=0.1,
        command=["pytest"],
        exit_code=1,
        junit_sha256="0" * 64,
    )


@pytest.mark.parametrize(
    ("outcomes", "kinds", "status"),
    [
        ({"a": "pass", "b": "pass"}, {}, "survived"),
        ({"a": "fail"}, {"a": "ASSERTION"}, "killed"),
        ({"a": "error"}, {"a": "EXCEPTION_BEHAVIOURAL"}, "killed"),
        ({"a": "timeout"}, {"a": "TIMEOUT"}, "timeout"),
        ({"a": "error", "b": "error"}, {"a": "COLLECTION", "b": "COLLECTION"}, "invalid"),
        ({"a": "error"}, {"a": "ENV"}, "error"),
        ({}, {}, "error"),
    ],
)
def test_fr_mu_10_outcome_classification(
    outcomes: dict[str, TestOutcome], kinds: dict[str, str], status: str
) -> None:
    assert classify_run(_result(outcomes, kinds)).status == status


class ScriptedAdapter:
    """Kills mutants whose replacement contains a marker; can be flaky on re-run."""

    name = "scripted"

    def __init__(self, flaky_marker: str | None = None) -> None:
        self.flaky_marker = flaky_marker
        self.seen: list[tuple[str, bool]] = []

    def run(self, ws: Workspace, tests: list[str], *, fail_fast: bool, **_: Any) -> RunResult:
        text = (ws.root / "src" / "pricing.py").read_text(encoding="utf-8")
        self.seen.append((text, fail_fast))
        if "<=" in text:
            return _result(dict.fromkeys(tests, "fail"), dict.fromkeys(tests, "ASSERTION"))
        if self.flaky_marker and self.flaky_marker in text and not fail_fast:
            return _result(dict.fromkeys(tests, "fail"), dict.fromkeys(tests, "ASSERTION"))
        return _result(dict.fromkeys(tests, "pass"))


@pytest.fixture
def worker(tmp_path: Path) -> Workspace:
    root = tmp_path / "w1"
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(SOURCE, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "head")
    return Workspace("w1", root, "h" * 40)


def test_fr_mu_10_runner_kills_survives_and_resets_files(worker: Workspace, tmp_path: Path) -> None:
    jobs = [
        MutantJob(_mutant(id="r001"), ["t::a"], 10),
        MutantJob(_mutant(id="r002", replacement="    if total > 0:\n"), ["t::a"], 10),
    ]
    adapter = ScriptedAdapter()
    results = run_mutants(
        jobs,
        adapter=adapter,  # type: ignore[arg-type]
        workers=[worker],
        head_text=lambda _: SOURCE,
        junit_path=lambda name: tmp_path / f"{name}.xml",
    )
    assert [(m.id, m.status, m.killed_by) for m in results] == [
        ("r001", "killed", ["t::a"]),
        ("r002", "survived", []),
    ]
    # FR-MU-11: the survivor was re-run once, without fail-fast.
    assert [fail_fast for _, fail_fast in adapter.seen] == [True, True, False]
    # FR-WS-03: the worker's file is reset after every mutant.
    assert (worker.root / "src" / "pricing.py").read_text(encoding="utf-8") == SOURCE


def test_fr_mu_11_flaky_survivor_is_excluded(worker: Workspace, tmp_path: Path) -> None:
    job = MutantJob(_mutant(id="r003", replacement="    if total > 0:\n"), ["t::a"], 10)
    results = run_mutants(
        [job],
        adapter=ScriptedAdapter(flaky_marker="total > 0"),  # type: ignore[arg-type]
        workers=[worker],
        head_text=lambda _: SOURCE,
        junit_path=lambda name: tmp_path / f"{name}.xml",
    )
    assert results[0].status == "error"


def test_fr_mu_12_kill_ratio_counts_timeouts_and_ignores_errors() -> None:
    config = build_config()
    mutants = [
        _mutant(id="r1", status="killed"),
        _mutant(id="r2", status="timeout"),
        _mutant(id="r3", status="survived"),
        _mutant(id="r4", status="error"),
        _mutant(id="r5", status="invalid"),
    ]
    result = _score(mutants, config, {"candidates": 9, "equivalent": 1, "invalid": 2}, 1.0)
    assert result.metrics["valid"] == 3
    assert result.metrics["kill_ratio"] == pytest.approx(2 / 3)
    assert result.metrics["kill_ratio_rule"] == pytest.approx(2 / 3)
    assert result.signals == ["MU_PARTIAL"]
    assert [f.rule_id for f in result.findings] == ["MU-SURVIVOR"]
    assert result.findings[0].severity == "low"


def test_fr_mu_12_no_valid_mutants() -> None:
    result = _score([], build_config(), {}, 0.0)
    assert result.status == "inconclusive"
    assert result.signals == ["MU_NO_MUTANTS", "MU_FEW_MUTANTS"]
    assert "kill_ratio" not in result.metrics


def test_fr_mu_05_soft_diversity_fills_the_budget_for_one_line_changes() -> None:
    one_line = _mutants({11})  # five candidates, all on line 11
    assert len(one_line) >= 4
    chosen = select_mutants(one_line, 4, b"" * 16)
    assert len(chosen) == 4, "the budget is filled once diversity is exhausted"
    spread = select_mutants(_candidates(), 6, b"" * 16)
    assert max(sum(1 for c in spread if c.line == line) for line in {c.line for c in spread}) <= 2


class LoadedAdapter:
    """Times out the first ``slow`` calls (machine load), then reports ``outcome``."""

    name = "loaded"

    def __init__(self, slow: set[int], outcome: TestOutcome = "pass") -> None:
        self.slow = slow
        self.outcome = outcome
        self.calls: list[float] = []

    def run(self, ws: Workspace, tests: list[str], *, timeout_s: float, **_: Any) -> RunResult:
        self.calls.append(timeout_s)
        if len(self.calls) in self.slow:
            return _result(dict.fromkeys(tests, "timeout"), dict.fromkeys(tests, "TIMEOUT"))
        kinds = {} if self.outcome == "pass" else dict.fromkeys(tests, "ASSERTION")
        return _result(dict.fromkeys(tests, self.outcome), kinds)


def _run_loaded(worker: Workspace, tmp_path: Path, adapter: LoadedAdapter) -> str:
    job = MutantJob(_mutant(id="r009", replacement="    if total > 0:\n"), ["t::a"], 10)
    (result,) = run_mutants(
        [job],
        adapter=adapter,  # type: ignore[arg-type]
        workers=[worker],
        head_text=lambda _: SOURCE,
        junit_path=lambda name: tmp_path / f"{name}.xml",
    )
    return result.status


def test_fr_mu_10_a_load_timeout_on_the_rerun_does_not_hide_a_survivor(
    worker: Workspace, tmp_path: Path
) -> None:
    """Regression (M8 eval): under heavy parallel load the FR-MU-11 re-run of a survivor
    timed out and the survivor was dropped as 'flaky', turning WEAK into PROVEN."""
    adapter = LoadedAdapter(slow={2})
    assert _run_loaded(worker, tmp_path, adapter) == "survived"
    assert adapter.calls == [10, 10, 20]  # the timed-out re-run is retried with more time


def test_fr_mu_10_a_first_timeout_is_confirmed_before_it_counts_as_a_kill(
    worker: Workspace, tmp_path: Path
) -> None:
    adapter = LoadedAdapter(slow={1})
    assert _run_loaded(worker, tmp_path, adapter) == "survived"
    hang = LoadedAdapter(slow={1, 2})
    assert _run_loaded(worker, tmp_path, hang) == "timeout"  # a real hang still kills
    assert hang.calls == [10, 20]
