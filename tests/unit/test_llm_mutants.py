"""LLM half of P3: FR-MU-03 forge validation, FR-MU-04 overfit, FR-MU-08 judge, FR-MU-09
fuzz, FR-MU-11 survivor re-judge and FR-MU-13 hints. Fake providers; fuzz runs Hypothesis."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from counterproof.config import build_config
from counterproof.llm.budget import BudgetGuard
from counterproof.llm.cache import LLMCache
from counterproof.llm.roles import LLMSession
from counterproof.llm.schemas import JudgeOutput
from counterproof.models import Mutant
from counterproof.probes.mutants.equivalence import (
    decide,
    enclosing_function,
    hint_mentions_mutation,
    judge_survivors,
    strip_comments_and_docstrings,
)
from counterproof.probes.mutants.fuzz import fuzz_target, make_fuzzer
from counterproof.probes.mutants.llm_forge import (
    forge,
    keeps_original_logic,
    literal_list,
    overfit_candidates,
    render_files,
)
from counterproof.probes.mutants.probe import _score
from tests.unit.test_llm import FakeProvider

SOURCE = dedent(
    '''\
    def percent_discount(total, percent, cap=None):
        """Discount, never more than cap."""
        discount = total * percent / 100
        if cap is not None and discount > cap:  # the fix
            return cap
        return discount
    '''
)
FILES = {"src/disc.py": SOURCE, "tests/test_disc.py": "def test_x():\n    assert True\n"}
SCOPE = {"src/disc.py": {4, 5}}


def _session(replies: list[str]) -> tuple[LLMSession, FakeProvider]:
    provider = FakeProvider(list(replies))
    return (
        LLMSession(
            provider=provider,
            cache=LLMCache(Path("unused"), "off"),
            budget=BudgetGuard(max_tokens=10**6, max_usd=1.0, max_seconds=600),
            prices={},
        ),
        provider,
    )


def _mutant(
    file: str, start: int, end: int, replacement: str, bug: str, plaus: int = 4
) -> dict[str, Any]:
    return {
        "id": "m",
        "file": file,
        "start_line": start,
        "end_line": end,
        "replacement": replacement,
        "bug_class": bug,
        "plausibility": plaus,
        "rationale": "because",
    }


def _forge(mutants: list[dict[str, Any]]) -> Any:
    s, provider = _session([json.dumps({"mutants": mutants})])
    result = forge(
        s,
        model="deepseek-v4-pro",
        scopes=SCOPE,
        changed=SCOPE,
        head_text=FILES.__getitem__,
        task_text="Cap the discount.",
        test_literals={("num", "25"), ("num", "200"), ("num", "1")},
        n=6,
        sendable=lambda p: True,
    )
    return result, provider


def test_fr_mu_03_prompt_shows_numbered_changed_lines_and_literals() -> None:
    _, provider = _forge([])
    prompt = provider.requests[0].user
    assert ">    4 |     if cap is not None and discount > cap:  # the fix" in prompt
    assert "     3 |     discount = total * percent / 100" in prompt
    assert "Cap the discount." in prompt
    system = provider.requests[0].system
    assert "200, 25" in system and "1," not in system  # trivial literals are left out
    assert "Include exactly one OVERFIT mutant" in system


def test_fr_mu_03_valid_mutants_become_llm_candidates() -> None:
    result, _ = _forge(
        [
            _mutant(
                "src/disc.py",
                4,
                4,
                "    if cap is not None and discount >= cap:",
                "wrong boundary",
                5,
            ),
            _mutant(
                "src/disc.py",
                4,
                5,
                "    if total == 200 and percent == 25:\n        return 30\n",
                "Overfit to test inputs",
                2,
            ),
        ]
    )
    assert [(c.operator, c.source, c.plausibility) for c in result.candidates] == [
        ("wrong boundary", "llm", 5),
        ("overfit", "llm", 2),
    ]
    assert result.candidates[0].replacement.endswith("\n")
    assert (
        result.candidates[0].original == "    if cap is not None and discount > cap:  # the fix\n"
    )
    assert overfit_candidates(result.candidates)[0].operator == "overfit"


@pytest.mark.parametrize(
    ("mutant", "reason"),
    [
        (
            _mutant("tests/test_disc.py", 2, 2, "    assert False\n", "x"),
            "file not among the changed source files",
        ),
        (
            _mutant("src/disc.py", 1, 1, "def percent_discount(total, percent, cap=0):\n", "x"),
            "outside the changed lines",
        ),
        (_mutant("src/disc.py", 2, 9, "x\n", "x"), "line range outside the file"),
        (
            _mutant(
                "src/disc.py", 4, 4, "    if cap is not None and discount > cap:  # the fix\n", "x"
            ),
            "identical to the original",
        ),
        (_mutant("src/disc.py", 4, 4, "    if cap is not None and:\n", "x"), "does not parse"),
    ],
)
def test_fr_mu_03_invalid_mutants_are_rejected(mutant: dict[str, Any], reason: str) -> None:
    result, _ = _forge([mutant])
    assert result.candidates == []
    assert result.rejected == {reason: 1}


def test_fr_mu_03_unsendable_files_are_never_sent() -> None:
    s, provider = _session([])
    result = forge(
        s,
        model="m",
        scopes={"secrets/.env": {1}},
        changed={},
        head_text=lambda p: "X=1\n",
        task_text=None,
        test_literals=set(),
        n=6,
        sendable=lambda p: not p.endswith(".env"),
    )
    assert not result.called and provider.requests == []


def test_fr_mu_03_forge_failure_degrades_to_rule_mutants() -> None:
    s, _ = _session(["nope", "still nope"])
    result = forge(
        s,
        model="m",
        scopes=SCOPE,
        changed=SCOPE,
        head_text=FILES.__getitem__,
        task_text=None,
        test_literals=set(),
        n=6,
        sendable=lambda p: True,
    )
    assert result.called and result.candidates == [] and not s.calls[-1].ok


def test_render_files_and_literal_list() -> None:
    text = render_files(SCOPE, SCOPE, FILES.__getitem__)
    assert text.startswith("### src/disc.py")
    assert literal_list({("str", "USD"), ("num", "0"), ("num", "2.5")}) == "'USD', 2.5"


def _survivor(**kw: Any) -> Mutant:
    base: dict[str, Any] = {
        "id": "l001",
        "source": "llm",
        "operator_or_class": "wrong boundary",
        "file": "src/disc.py",
        "start_line": 4,
        "end_line": 4,
        "original": "    if cap is not None and discount > cap:  # the fix\n",
        "replacement": "    if cap is not None and discount >= cap:\n",
        "status": "survived",
        "covering_tests": ["tests/test_disc.py::test_x"],
        "hint": "template hint",
    }
    base.update(kw)
    return Mutant.model_validate(base)


@pytest.mark.parametrize(
    ("judge", "fuzz", "status"),
    [
        (JudgeOutput(equivalent=True, confidence=0.9), None, "equivalent"),
        (JudgeOutput(equivalent=True, confidence=0.9), "same", "equivalent"),
        (JudgeOutput(equivalent=True, confidence=0.9), "differs", "survived"),
        (JudgeOutput(equivalent=True, confidence=0.5), "same", "probably_equivalent"),
        (JudgeOutput(equivalent=True, confidence=0.5), None, "survived"),
        (JudgeOutput(equivalent=False, confidence=0.9), "same", "survived"),
        (None, "same", "survived"),
    ],
)
def test_fr_mu_08_09_decision_table(
    judge: JudgeOutput | None, fuzz: str | None, status: str
) -> None:
    assert decide(judge, fuzz) == status  # type: ignore[arg-type]


def test_fr_mu_08_preprocessing_strips_comments_and_docstrings() -> None:
    function = enclosing_function(SOURCE, 4)
    assert function.startswith("def percent_discount")
    stripped = strip_comments_and_docstrings(function)
    assert (
        "the fix" not in stripped and "Docstring" not in stripped and "never more" not in stripped
    )
    assert "discount > cap" in stripped


def test_fr_mu_13_hint_must_mention_the_mutated_line() -> None:
    m = _survivor()
    assert hint_mentions_mutation("Test a discount exactly equal to the cap", m)
    assert not hint_mentions_mutation("Add more tests in general", m)


def _judge(replies: list[dict[str, Any]], mutant: Mutant) -> tuple[Mutant, FakeProvider]:
    s, provider = _session([json.dumps(r) for r in replies])
    (out,) = judge_survivors(
        [mutant],
        session=s,
        model="deepseek-flash",
        head_text=FILES.__getitem__,
        test_source=lambda ids: "def test_x():\n    assert percent_discount(200, 25, 30) == 30",
    )
    return out, provider


def test_fr_mu_11_survivor_judged_equivalent_is_excluded() -> None:
    out, provider = _judge([{"equivalent": True, "confidence": 0.95}], _survivor())
    assert out.status == "equivalent" and out.hint is None
    assert out.equivalence_note == "judge: equivalent (0.95)"
    user = provider.requests[0].user
    assert "the fix" not in user  # comments stripped (FR-MU-08)
    assert "assert percent_discount(200, 25, 30) == 30" in user  # tests shown (FR-MU-11)


def test_fr_mu_13_distinguishing_input_becomes_the_hint() -> None:
    hint = "percent_discount(100, 30, cap=30): discount equals cap, the mutant takes the cap branch"
    out, _ = _judge(
        [{"equivalent": False, "confidence": 0.8, "distinguishing_input": hint}], _survivor()
    )
    assert out.status == "survived" and out.hint == hint


def test_killed_mutants_are_never_judged() -> None:
    s, provider = _session([])
    (out,) = judge_survivors(
        [_survivor(status="killed")],
        session=s,
        model="m",
        head_text=FILES.__getitem__,
        test_source=lambda ids: "",
    )
    assert out.status == "killed" and provider.requests == []


def test_fr_mu_04_overfit_survivor_signal() -> None:
    result = _score(
        [
            _survivor(operator_or_class="overfit"),
            _survivor(id="r001", source="rule", status="killed"),
        ],
        build_config(),
        {"candidates": 3},
        1.0,
    )
    assert "MU_OVERFIT_SURVIVED" in result.signals
    assert result.metrics["kill_ratio_llm"] == 0.0 and result.metrics["kill_ratio_rule"] == 1.0


FUZZ_MODULE = dedent(
    """\
    from decimal import Decimal


    def scale(x: int, factor: Decimal) -> Decimal:
        return Decimal(x) * factor


    def describe(x):
        return str(x)
    """
)


def test_fr_mu_09_fuzz_target_needs_simple_annotated_top_level_function() -> None:
    assert fuzz_target(FUZZ_MODULE, 5) == ("scale", ["int", "Decimal"])
    assert fuzz_target(FUZZ_MODULE, 9) is None  # no annotations


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("    return Decimal(x) * factor * 2\n", "differs"),
        ("    return factor * Decimal(x)\n", "same"),
    ],
)
def test_fr_mu_09_differential_fuzz_with_hypothesis(
    tmp_path: Path, replacement: str, expected: str
) -> None:
    pkg = tmp_path / "src" / "fuzzpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "calc.py").write_text(FUZZ_MODULE, encoding="utf-8")

    def run_script(script: Path, args: list[str]) -> str | None:
        env = dict(os.environ, PYTHONPATH=str(tmp_path / "src"))
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else None

    fuzzer = make_fuzzer(
        head_text=lambda p: FUZZ_MODULE,
        literals={("num", "3"), ("num", "0.5")},
        run_script=run_script,
        script_dir=tmp_path / "fuzz",
    )
    mutant = _survivor(
        id="l009",
        file="src/fuzzpkg/calc.py",
        start_line=5,
        end_line=5,
        original="    return Decimal(x) * factor\n",
        replacement=replacement,
    )
    assert fuzzer(mutant) == expected


def test_regression_fuzz_and_judge_never_crash_on_a_mismatched_file(tmp_path: Path) -> None:
    """Regression (C1 recording, Windows CRLF worktree): the fuzzer re-read the head file
    with newline translation, the mutant's original text no longer matched, and the run
    crashed. A mutant that cannot be applied must degrade to 'no result', never raise."""
    crlf = SOURCE.replace("\n", "\r\n")
    mutant = _survivor(original="    if cap is not None and discount > cap:  # the fix\r\n")
    fuzzer = make_fuzzer(
        head_text=lambda p: SOURCE,  # LF view of a CRLF file: the original no longer matches
        literals=set(),
        run_script=lambda script, args: "differs",
        script_dir=tmp_path / "fuzz",
    )
    assert fuzzer(mutant) is None
    s, _ = _session([json.dumps({"equivalent": False, "confidence": 0.9})])
    (out,) = judge_survivors(
        [mutant],
        session=s,
        model="m",
        head_text=lambda p: SOURCE,
        test_source=lambda ids: "",
        fuzz=fuzzer,
    )
    assert out.status == "survived"
    assert crlf.count("\r\n") == SOURCE.count("\n")


def test_fr_mu_04_overfit_that_keeps_the_original_logic_is_reclassified() -> None:
    """The model sometimes labels "special case + original fallback" as overfit; FR-MU-04
    requires the general logic to be replaced, so such a mutant cannot raise rule 6."""
    fallback = (
        "    if total == 200:\n"
        "        return 30\n"
        "    if cap is not None and discount > cap:  # the fix\n"
        "        return cap\n"
    )
    replacing = "    if total == 200 and percent == 25:\n        return 30\n"
    result, _ = _forge(
        [
            _mutant("src/disc.py", 4, 5, fallback, "overfit"),
            _mutant("src/disc.py", 4, 5, replacing, "overfit"),
        ]
    )
    assert [c.operator for c in result.candidates] == [
        "special case (overfit with fallback)",
        "overfit",
    ]
    assert keeps_original_logic("    a = 1\n    b = 2\n", "    x = 0\n    a = 1\n    b = 2\n")
    assert not keeps_original_logic("    a = 1\n    b = 2\n", "    a = 1\n")
