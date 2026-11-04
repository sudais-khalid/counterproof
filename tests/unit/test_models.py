from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from counterproof.models import (
    VERDICT_EXIT_CODES,
    ChangeSet,
    Cost,
    Finding,
    Mutant,
    ProbeResult,
    RunManifest,
    TestId,
    Verdict,
)


@pytest.mark.parametrize(
    ("name", "code"),
    [("PROVEN", 0), ("WEAK", 10), ("UNPROVEN", 20), ("REFUTED", 30), ("INCONCLUSIVE", 40)],
)
def test_verdict_exit_codes_match_prd_section_5_2(name: str, code: int) -> None:
    assert VERDICT_EXIT_CODES[name] == code  # type: ignore[index]
    assert Verdict.make(name, "R-TEST").exit_code == code  # type: ignore[arg-type]


def test_verdict_rejects_exit_code_inconsistent_with_verdict() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        Verdict(verdict="PROVEN", rule_id="R-PROVEN", exit_code=40)


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Cost(tokens_in=1, surprise=2)  # type: ignore[call-arg]


def test_mutant_plausibility_is_bounded_1_to_5() -> None:
    base = {
        "id": "m1",
        "source": "llm",
        "operator_or_class": "off_by_one",
        "file": "a.py",
        "start_line": 3,
        "end_line": 3,
        "original": "x < y",
        "replacement": "x <= y",
        "status": "survived",
    }
    assert Mutant.model_validate({**base, "plausibility": 5}).killed_by == []
    with pytest.raises(ValidationError):
        Mutant.model_validate({**base, "plausibility": 6})


def test_test_id_nodeid() -> None:
    tid = TestId(adapter="pytest", file="tests/test_cart.py", name="test_discount")
    assert tid.nodeid == "tests/test_cart.py::test_discount"


def _manifest() -> RunManifest:
    change = ChangeSet(
        repo_root=Path("/repo"),
        base_sha="a" * 40,
        head_ref="WORKTREE",
        head_snapshot_sha="",
        intent="bugfix",
        intent_source="rule",
        flags=["self_config_change"],
    )
    probe = ProbeResult(
        probe="integrity",
        status="ok",
        signals=["IN_CLEAN"],
        findings=[Finding(rule_id="FR-IN-04", severity="medium", message="loosened")],
        metrics={"findings": 1.0},
    )
    return RunManifest(
        run_id="20261104T110000Z-0a1b2c3d",
        tool_version="0.1.0",
        created_at=datetime(2026, 11, 4, 11, 0, tzinfo=UTC),
        seed_commitment="b" * 64,
        config_sha256="c" * 64,
        change=change,
        probes=[probe],
        verdict=Verdict.make("WEAK", "R-WEAK-MEDIUM", ["medium finding"]),
        artefacts={"config.json": "d" * 64},
    )


def test_run_manifest_json_round_trip() -> None:
    manifest = _manifest()
    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert restored.probes[0].cost == Cost()
