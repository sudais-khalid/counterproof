"""P4 blind oracle: FR-BO-01 interface (never a body, asserted on the prompt itself),
FR-BO-02 writer validation, FR-BO-03/04 runner and triage, FR-BO-05 never refutes,
FR-BO-06 export, FR-BO-07 model choice. Fake providers and adapters; no network."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import libcst as cst
import pytest

from counterproof import git
from counterproof.change.resolver import resolve_change
from counterproof.change.task import TaskText
from counterproof.config import build_config
from counterproof.errors import UsageError
from counterproof.llm.budget import BudgetGuard
from counterproof.llm.cache import LLMCache
from counterproof.llm.provider import choose_oracle_models, model_family
from counterproof.llm.roles import LLMSession, load_prompt
from counterproof.llm.schemas import OracleOutput
from counterproof.models import ChangeSet, FailureInfo, FailureKind, RunResult, TestOutcome
from counterproof.probes.blind_oracle import export as oracle_export
from counterproof.probes.blind_oracle.interface import (
    CHANGED,
    Interface,
    changed_function_sources,
    extract_interface,
)
from counterproof.probes.blind_oracle.probe import (
    BO_AGREE,
    BO_ALL_DISCARDED,
    BO_FAILURES,
    BO_LIKELY_BUG,
    BO_NO_TESTS,
    LIKELY_BUG_RULE,
    run_blind_oracle,
)
from counterproof.probes.blind_oracle.runner import run_oracle
from counterproof.probes.blind_oracle.writer import OracleSuite, validate, write_oracle_tests
from counterproof.verdict.rules import DecisionInput, decide
from counterproof.workspace.manager import Workspace
from tests.helpers import FIXTURE_REPOS, all_variants, build_repo
from tests.helpers import git as run_git
from tests.unit.test_llm import FakeProvider

LEDGER_TESTS = [
    "tests/test_discounts.py",
    "tests/test_invoice.py",
    "tests/test_money.py",
    "tests/test_rounding.py",
    "tests/test_tax.py",
]
WRITER_REPLY = json.dumps(
    {
        "imports": ["import pytest"],
        "tests": [{"name": "test_one", "intent": "one", "code": "def test_one():\n    assert 1\n"}],
    }
)


def _session(replies: Sequence[str]) -> tuple[LLMSession, FakeProvider]:
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


def _change(tmp_path: Path, task: str, variant: str, overlay: Path | None = None) -> ChangeSet:
    repo = build_repo(tmp_path / "repo", task=task, variant=variant, overlay=overlay)
    assert repo.head_sha is not None
    return resolve_change(
        repo.path,
        base_sha=repo.base_sha,
        head_ref=repo.head_sha,
        head_sha=repo.head_sha,
        task=TaskText(repo.task_text, "cli"),
    )


def _readers(change: ChangeSet) -> tuple[Any, Any]:
    return (
        partial(git.show_file, change.repo_root, change.head_snapshot_sha),
        partial(git.show_file, change.repo_root, change.base_sha),
    )


def _body_lines(text: str) -> set[str]:
    """Every non-docstring line inside any function body of a module."""
    module = cst.parse_module(text)
    lines: set[str] = set()

    class Visitor(cst.CSTVisitor):
        def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
            body = list(node.body.body) if isinstance(node.body, cst.IndentedBlock) else []
            if body and node.get_docstring() is not None:
                body = body[1:]
            for stmt in body:
                for line in module.code_for_node(stmt).splitlines():
                    if len(line.strip()) >= 4:
                        lines.add(line.strip())

    module.visit(Visitor())
    return lines


# -- FR-BO-01: what the oracle sees --------------------------------------------------------


@pytest.mark.parametrize(("task", "variant"), [*all_variants(), ("T1-discount-cap", "c2-broken")])
def test_fr_bo_01_oracle_prompt_never_contains_a_function_body(
    tmp_path: Path, task: str, variant: str
) -> None:
    """M5 acceptance: the prompt actually sent to the oracle model holds no body line of any
    function in any source module (changed or imported), on every fixture variant."""
    overlay = FIXTURE_REPOS / "py_ledger" / "c2" / "broken-cap" if variant == "c2-broken" else None
    change = _change(tmp_path, task, "honest" if overlay else variant, overlay)
    read_head, read_base = _readers(change)
    interface = extract_interface(
        change, read_head=read_head, read_base=read_base, selected_test_files=LEDGER_TESTS
    )
    session, provider = _session([WRITER_REPLY])
    write_oracle_tests(session, models=["m"], task_text=change.task_text, interface=interface, k=6)
    (request,) = provider.requests
    prompt = request.system + "\n" + request.user

    assert interface.changed_symbols and CHANGED in prompt
    sources = run_git(change.repo_root, "ls-tree", "-r", "--name-only", change.head_snapshot_sha)
    leaked = []
    for path in sources.splitlines():
        if path.startswith("src/") and path.endswith(".py"):
            leaked += [ln for ln in _body_lines(read_head(path) or "") if ln in prompt]
    assert leaked == []
    # The style example is the base version, never the agent's own tests.
    assert interface.style_path is not None
    base_tests = read_base(interface.style_path) or ""
    head_tests = read_head(interface.style_path) or ""
    agents = [
        ln for ln in head_tests.splitlines() if ln.startswith("def ") and ln not in base_tests
    ]
    assert [ln for ln in agents if ln in prompt] == []


def test_fr_bo_01_interface_marks_changes_and_hides_private_names(tmp_path: Path) -> None:
    change = _change(tmp_path, "T3-allocate-remainder", "honest")
    read_head, read_base = _readers(change)
    interface = extract_interface(
        change, read_head=read_head, read_base=read_base, selected_test_files=LEDGER_TESTS
    )
    assert interface.changed_symbols == {"ledger.money": ["Money.allocate"]}
    stub = interface.modules["ledger.money"]
    assert "def allocate(self, ratios: list[int]) -> list[Money]:" in stub
    assert "_check" not in stub  # private helper: not part of the interface
    assert "def __add__" in stub  # operators are
    assert interface.style_path == "tests/test_money.py" and interface.test_dir == "tests"
    assert "Money" in interface.public_names["ledger.money"]


def test_fr_bo_01_excluded_paths_never_reach_the_interface(tmp_path: Path) -> None:
    change = _change(tmp_path, "T1-discount-cap", "honest")
    read_head, read_base = _readers(change)
    interface = extract_interface(
        change,
        read_head=read_head,
        read_base=read_base,
        selected_test_files=LEDGER_TESTS,
        sendable=lambda p: not p.endswith("discounts.py"),
    )
    assert interface.empty and "ledger.discounts" not in interface.modules
    assert "test_discounts" not in (interface.style_path or "")
    assert changed_function_sources(change, read_head, sendable=lambda _p: False) == {}
    assert set(changed_function_sources(change, read_head)) == {"percent_discount"}


# -- FR-BO-02: writer validation -----------------------------------------------------------

IFACE = Interface(
    modules={"pkg.mod": "def f(x: int) -> int: ...\n"},
    changed_symbols={"pkg.mod": ["f"]},
    public_names={"pkg.mod": frozenset({"f", "Decimal"})},
)


def _output(tests: list[tuple[str, str]], imports: list[str] | None = None) -> OracleOutput:
    return OracleOutput.model_validate(
        {
            "imports": imports
            if imports is not None
            else ["import pytest", "from pkg.mod import f"],
            "tests": [{"name": n, "intent": f"checks {n}", "code": c} for n, c in tests],
        }
    )


def test_fr_bo_02_valid_tests_get_an_intent_line_and_unique_names() -> None:
    suite = validate(
        _output(
            [
                ("test_a", "# intent: old comment\ndef test_a():\n    assert f(1) == 2\n"),
                ("a", "def test_a():\n    with pytest.raises(ValueError):\n        f(-1)\n"),
            ]
        ),
        IFACE,
        k=6,
    )
    assert [c.name for c in suite.cases] == ["test_oracle_a", "test_oracle_a_2"]
    assert suite.cases[0].code.startswith("# intent: checks test_a\ndef test_oracle_a():")
    assert "old comment" not in suite.cases[0].code
    text = suite.text("header")
    assert text.startswith('"""header"""\n\nimport pytest\nfrom pkg.mod import f\n')
    compile(text, "oracle.py", "exec")


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("def test_x():\n    assert f(\n", "syntax error"),
        ("def helper():\n    pass\n\ndef test_x():\n    assert f(1)\n", "exactly one"),
        ("x = 1\ndef test_x():\n    assert f(x)\n", "exactly one"),
        ("def test_x():\n    from pkg.mod import _secret\n    assert _secret\n", "private name"),
        ("def test_x():\n    import os\n    assert os.getcwd()\n", "imports os"),
        ("def test_x():\n    assert open('f').read()\n", "calls open()"),
        ("def test_x():\n    assert f(1)._cache\n", "private attribute"),
        ("def test_x():\n    from pkg.other import g\n    assert g()\n", "not in the interface"),
        ("def test_x():\n    assert f(Decimal('1'))\n", "undefined name(s) Decimal"),
    ],
)
def test_fr_bo_02_invalid_tests_are_rejected(code: str, reason: str) -> None:
    suite = validate(_output([("test_x", code)]), IFACE, k=6)
    assert suite.cases == []
    assert reason in suite.rejected[0][1]


def test_fr_bo_02_imports_are_checked_and_k_caps_the_suite() -> None:
    out = _output(
        [(f"test_{i}", f"def test_{i}():\n    assert f({i}) is not None\n") for i in range(9)],
        imports=["from pkg.mod import f", "import subprocess", "from pkg.mod import nope", "x = 1"],
    )
    suite = validate(out, IFACE, k=6)
    assert len(suite.cases) == 6
    assert suite.imports == ["from pkg.mod import f"]
    assert [r for _, r in suite.rejected] == [
        "imports subprocess",
        "imports nope from pkg.mod: not in the interface",
        "not a single import: 'x = 1'",
    ]


def test_fr_bo_02_prompt_files_and_contract() -> None:
    writer = load_prompt("oracle_writer")
    assert "BEFORE seeing any implementation" in writer.system
    assert "# intent:" in writer.system and "{{interface}}" in writer.user
    triage = load_prompt("triage_judge")
    assert '"likely_bug"' in triage.system and "{{base_outcome}}" in triage.user


# -- FR-BO-03 / FR-BO-04: runner, triage, findings -------------------------------------


@dataclass
class FakeAdapter:
    """Outcome per test name for each successive run; base runs use ``base``."""

    head: list[dict[str, TestOutcome]]
    base: dict[str, TestOutcome] = field(default_factory=dict)
    kinds: dict[str, FailureKind] = field(default_factory=dict)
    calls: list[tuple[str, list[str], bool]] = field(default_factory=list)

    def run(self, ws: Workspace, tests: list[str], **_: Any) -> RunResult:
        is_base = ws.name == "base"
        rel = tests[0].partition("::")[0]
        self.calls.append((ws.name, list(tests), (ws.root / rel).is_file()))
        n = sum(1 for c in self.calls if c[0] == ws.name)
        plan = self.base if is_base else self.head[n - 1]
        outcomes = {t: plan[t.rsplit("::", 1)[-1]] for t in tests}
        failures = {
            t: FailureInfo(
                kind=self.kinds.get(t.rsplit("::", 1)[-1], "ASSERTION"), message="assert 1 == 2"
            )
            for t, o in outcomes.items()
            if o in {"fail", "error"}
        }
        return RunResult(
            tests=outcomes,
            failures=failures,
            duration_s=0.1,
            command=["pytest"],
            exit_code=0,
            junit_sha256="0",
        )


def _suite(*names: str) -> OracleSuite:
    out = _output([(n, f"def {n}():\n    assert f(1) == 1\n") for n in names])
    return validate(out, IFACE, k=10)


def _workspaces(tmp_path: Path) -> tuple[Workspace, Workspace]:
    head, base = tmp_path / "head", tmp_path / "base"
    (head / "tests").mkdir(parents=True)
    (base / "tests").mkdir(parents=True)
    return Workspace("head", head, "h"), Workspace("base", base, "b")


def test_fr_bo_03_three_head_runs_then_failing_tests_on_base(tmp_path: Path) -> None:
    head, base = _workspaces(tmp_path)
    plan: dict[str, TestOutcome] = {
        "test_oracle_ok": "pass",
        "test_oracle_bad": "fail",
        "test_oracle_flaky": "pass",
        "test_oracle_env": "error",
    }
    flaky_run: dict[str, TestOutcome] = {**plan, "test_oracle_flaky": "fail"}
    adapter = FakeAdapter(
        head=[plan, flaky_run, plan],
        base={"test_oracle_bad": "pass"},
        kinds={"test_oracle_env": "ENV"},
    )
    rel, results = run_oracle(
        adapter,  # type: ignore[arg-type]
        head=head,
        base=base,
        suite=_suite("test_ok", "test_bad", "test_flaky", "test_env"),
        test_dir="tests",
        header="h",
        junit_path=lambda n: tmp_path / f"{n}.xml",
        timeout_s=10,
    )
    assert rel == "tests/test_counterproof_oracle.py"
    status = {r.case.name: (r.status, r.reason, r.base_outcome) for r in results}
    assert status == {
        "test_oracle_ok": ("agree", "", None),
        "test_oracle_bad": ("failing", "", "pass"),
        "test_oracle_flaky": ("discarded", "flaky on head", None),
        "test_oracle_env": ("discarded", "env error", None),
    }
    assert [c[0] for c in adapter.calls] == ["head", "head", "head", "base"]
    assert adapter.calls[-1][1] == [f"{rel}::test_oracle_bad"]
    assert all(present for _, _, present in adapter.calls)  # file exists while it runs
    assert not (head.root / rel).exists() and not (base.root / rel).exists()  # removed after


def test_fr_bo_03_an_existing_file_name_is_never_overwritten(tmp_path: Path) -> None:
    head, base = _workspaces(tmp_path)
    (head.root / "tests" / "test_counterproof_oracle.py").write_text("# real\n", encoding="utf-8")
    adapter = FakeAdapter(head=[{"test_oracle_ok": "pass"}] * 3)
    rel, _ = run_oracle(
        adapter,  # type: ignore[arg-type]
        head=head,
        base=base,
        suite=_suite("test_ok"),
        test_dir="tests",
        header="h",
        junit_path=lambda n: tmp_path / f"{n}.xml",
        timeout_s=10,
    )
    assert rel == "tests/test_counterproof_oracle_2.py"
    assert (head.root / "tests" / "test_counterproof_oracle.py").read_text(
        encoding="utf-8"
    ) == "# real\n"


def _triage(label: str, confidence: float) -> str:
    return json.dumps({"label": label, "confidence": confidence, "explanation": f"{label} because"})


def _writer(*names: str) -> str:
    return json.dumps(
        {
            "imports": ["from pkg.mod import f"],
            "tests": [
                {
                    "name": n,
                    "intent": f"requirement {n}",
                    "code": f"def {n}():\n    assert f(1) == 1\n",
                }
                for n in names
            ],
        }
    )


def _probe(tmp_path: Path, replies: list[str], head_plan: dict[str, TestOutcome]) -> Any:
    head, base = _workspaces(tmp_path)
    session, provider = _session(replies)
    outcome = run_blind_oracle(
        session=session,
        models=["writer-model"],
        model_note="n",
        judge_model="judge-model",
        k=6,
        task_text="f must return 1",
        interface=IFACE,
        functions={"f": "def f(x):\n    return 2\n"},
        adapter=FakeAdapter(head=[head_plan] * 3, base=dict.fromkeys(head_plan, "fail")),  # type: ignore[arg-type]
        head=head,
        base=base,
        run_id="r1",
        junit_path=lambda n: tmp_path / f"{n}.xml",
        timeout_s=10,
    )
    return outcome, provider


def test_fr_bo_04_only_confident_likely_bugs_become_findings(tmp_path: Path) -> None:
    plan: dict[str, TestOutcome] = {
        "test_oracle_a": "fail",
        "test_oracle_b": "fail",
        "test_oracle_c": "fail",
        "test_oracle_d": "fail",
        "test_oracle_e": "pass",
    }
    replies = [
        _writer("test_a", "test_b", "test_c", "test_d", "test_e"),
        _triage("likely_bug", 0.9),
        _triage("likely_bug", 0.7),
        _triage("oracle_wrong", 0.95),
    ]
    outcome, provider = _probe(tmp_path, replies, plan)
    result = outcome.result
    assert result.signals == [BO_FAILURES, BO_LIKELY_BUG]
    assert [f.rule_id for f in result.findings] == [LIKELY_BUG_RULE]
    assert "test_oracle_a (requirement test_a)" in result.findings[0].message
    assert result.metrics["likely_bug"] == 1 and result.metrics["untriaged"] == 1  # max 3 calls
    assert [r.role for r in provider.requests] == ["oracle_writer", *["triage_judge"] * 3]
    judge = provider.requests[1]
    assert judge.model == "judge-model" and "return 2" in judge.user and "also FAILED" in judge.user
    record = outcome.record
    assert record["tests"][0]["triage"] == {
        "label": "likely_bug",
        "confidence": 0.9,
        "explanation": "likely_bug because",
    }
    assert outcome.test_file is not None and "def test_oracle_e():" in outcome.test_file


def test_fr_bo_05_oracle_findings_cap_at_weak_and_never_refute(tmp_path: Path) -> None:
    outcome, _ = _probe(
        tmp_path, [_writer("test_a"), _triage("likely_bug", 0.99)], {"test_oracle_a": "fail"}
    )
    config = build_config({}, {})
    mutants = _mutants_probe()
    rg = _rg_probe()
    verdict = decide(
        DecisionInput("bugfix", probes=[rg, mutants, outcome.result], llm_enabled=True), config
    )
    assert (verdict.verdict, verdict.rule_id) == ("WEAK", "R-WEAK-ORACLE")
    without = decide(DecisionInput("bugfix", probes=[rg, mutants], llm_enabled=True), config)
    assert without.verdict == "PROVEN"


def _mutants_probe() -> Any:
    from counterproof.models import ProbeResult  # noqa: PLC0415

    return ProbeResult(
        probe="mutants", status="ok", metrics={"valid": 8, "killed": 8, "kill_ratio": 1.0}
    )


def _rg_probe() -> Any:
    from counterproof.models import ProbeResult  # noqa: PLC0415

    return ProbeResult(probe="red_green", status="ok", signals=["RG_STRONG"])


def test_fr_bo_03_no_valid_tests_or_all_discarded_is_inconclusive(tmp_path: Path) -> None:
    bad = json.dumps({"imports": [], "tests": [{"name": "t", "intent": "i", "code": "import os"}]})
    outcome, _ = _probe(tmp_path / "a", [bad], {})
    assert (outcome.result.status, outcome.result.signals) == ("inconclusive", [BO_NO_TESTS])
    outcome, _ = _probe(tmp_path / "b", [_writer("test_a")], {"test_oracle_a": "skip"})
    assert outcome.result.signals == [BO_ALL_DISCARDED]
    outcome, _ = _probe(tmp_path / "c", [_writer("test_a")], {"test_oracle_a": "pass"})
    assert outcome.result.signals == [BO_AGREE] and outcome.result.findings == []


# -- FR-BO-06: export ----------------------------------------------------------------------


def _record() -> dict[str, Any]:
    return {
        "test_dir": "tests",
        "imports": ["from pkg.mod import f"],
        "tests": [
            {
                "name": "test_oracle_a",
                "intent": "a",
                "status": "agree",
                "reason": "",
                "triage": None,
                "code": "# intent: a\ndef test_oracle_a():\n    assert f(1) == 1\n",
            },
            {
                "name": "test_oracle_b",
                "intent": "b",
                "status": "failing",
                "reason": "",
                "triage": {"label": "likely_bug", "confidence": 0.9, "explanation": "x"},
                "code": "# intent: b\ndef test_oracle_b():\n    assert f(2) == 2\n",
            },
            {
                "name": "test_oracle_c",
                "intent": "c",
                "status": "discarded",
                "reason": "env error",
                "triage": None,
                "code": "def test_oracle_c(): ...\n",
            },
        ],
    }


def test_fr_bo_06_export_lists_then_writes_only_accepted_tests(tmp_path: Path) -> None:
    record = _record()
    lines = oracle_export.describe(record)
    assert lines == [
        "test_oracle_a  [agree]  a",
        "test_oracle_b  [failing: likely_bug 0.90]  b",
        "test_oracle_c  [discarded: env error]  c",
    ]
    target, names = oracle_export.export(
        record, "20261107T000000Z-abcd1234", ["test_oracle_b"], tmp_path
    )
    assert target.name == "test_counterproof_oracle_abcd1234.py" and names == ["test_oracle_b"]
    text = target.read_text(encoding="utf-8")
    assert "def test_oracle_b" in text and "def test_oracle_a" not in text
    assert "from pkg.mod import f" in text
    with pytest.raises(UsageError, match="already exists"):
        oracle_export.export(record, "20261107T000000Z-abcd1234", ["all"], tmp_path)
    with pytest.raises(UsageError, match="test_oracle_c"):
        oracle_export.export(record, "20261107T000000Z-ffff0000", ["test_oracle_c"], tmp_path)
    target, names = oracle_export.export(record, "20261107T000000Z-ffff0000", ["all"], tmp_path)
    assert names == ["test_oracle_a", "test_oracle_b"]


# -- FR-BO-07: model choice ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "author", "count", "expected", "how"),
    [
        ("deepseek", "claude-sonnet-5-5", 1, ["deepseek-v4-pro"], "different family"),
        ("deepseek", "deepseek-v4-pro", 1, ["deepseek-flash"], "same family, different model"),
        ("anthropic", "claude-sonnet-5-5", 1, ["claude-opus-5-5"], "same family, different model"),
        ("anthropic", "gpt-5", 1, ["claude-sonnet-5-5"], "different family"),
        ("anthropic", None, 2, ["claude-sonnet-5-5", "claude-opus-5-5"], "author unknown"),
        ("ollama", "qwen2.5-coder:7b", 1, ["qwen2.5-coder:7b"], "same model"),
    ],
)
def test_fr_bo_07_oracle_model_differs_from_the_author(
    provider: str, author: str | None, count: int, expected: list[str], how: str
) -> None:
    models, note = choose_oracle_models(provider, "auto", author, count)
    assert models == expected and how in note


def test_fr_bo_07_configured_model_wins_and_families() -> None:
    models, note = choose_oracle_models("deepseek", "my-model", "my-model", 1)
    assert models == ["my-model"] and "also the code author's model" in note
    assert model_family("claude-opus-5-5") == "anthropic"
    assert model_family("openai/gpt-5") == "openai"
    assert model_family("qwen2.5-coder:7b") == "qwen"


def test_triage_prompt_is_identical_on_every_platform() -> None:
    """Regression (M5 CI): the failure text carried Windows path separators and line
    endings, so triage cassettes recorded on Windows missed on Linux and macOS."""
    from counterproof.probes.blind_oracle.runner import OracleResult  # noqa: PLC0415
    from counterproof.probes.blind_oracle.triage import triage_failures  # noqa: PLC0415

    case = _suite("test_a").cases[0]
    prompts = []
    for sep, eol in (("\\", "\r\n"), ("/", "\n")):
        failure = FailureInfo(
            kind="ASSERTION",
            message="AssertionError: assert 2 == 1",
            traceback_excerpt=eol.join(
                [
                    "def test_oracle_a():",
                    ">       assert f(1) == 1",
                    "E       assert 2 == 1",
                    "",
                    f"tests{sep}test_counterproof_oracle.py:7: AssertionError",
                ]
            ),
        )
        result = OracleResult(
            case=case,
            nodeid="n",
            status="failing",
            failure=failure,
            base_outcome="fail",
            base_failure=failure,
        )
        session, provider = _session([_triage("oracle_wrong", 0.9)])
        triage_failures(session, model="j", results=[result], task_text="t", functions={})
        prompts.append(provider.requests[0].user)
    assert prompts[0] == prompts[1]
    assert "tests/test_counterproof_oracle.py:7" in prompts[1]
