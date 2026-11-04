from __future__ import annotations

import os
from pathlib import Path

import pytest

from counterproof.config import (
    PROFILES,
    Config,
    build_config,
    default_workers,
    load_config,
    parse_toml,
)
from counterproof.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_defaults_are_local_first() -> None:
    cfg = build_config()
    assert cfg.llm.provider == "none"
    assert cfg.run.profile == "full"
    assert cfg.run.mode == "audit"
    assert cfg.run.short_circuit is True
    assert cfg.mutants.budget == 8
    assert cfg.impact.baseline_runs == 3
    assert cfg.flaky.max_ratio == 0.2
    assert cfg.mutants.unproven_below == 0.5
    assert cfg.mutants.proven_at_least == 0.8
    assert cfg.oracle.k == 6
    assert cfg.impact.max_tests == 400


@pytest.mark.parametrize(
    ("profile", "budget", "baseline", "has_oracle"),
    [("fast", 4, 2, False), ("full", 8, 3, True), ("paranoid", 16, 5, True)],
)
def test_profile_defaults_section_5_4(
    profile: str, budget: int, baseline: int, has_oracle: bool
) -> None:
    cfg = build_config({"run": {"profile": profile}})
    assert cfg.mutants.budget == budget
    assert cfg.impact.baseline_runs == baseline
    assert ("blind_oracle" in cfg.enabled_probes) is has_oracle
    assert cfg.profile_spec is PROFILES[profile]  # type: ignore[index]


def test_file_values_override_profile_defaults() -> None:
    cfg = build_config({"run": {"profile": "fast"}, "mutants": {"budget": 6}})
    assert cfg.mutants.budget == 6
    assert cfg.impact.baseline_runs == 2


def test_cli_overrides_beat_file_values() -> None:
    cfg = build_config(
        {"run": {"profile": "full", "workers": 2}, "llm": {"provider": "anthropic"}},
        {"run": {"profile": "fast", "workers": 3}, "llm": {"provider": "none"}},
    )
    assert cfg.run.profile == "fast"
    assert cfg.run.workers == 3
    assert cfg.llm.provider == "none"
    assert cfg.mutants.budget == 4


def test_unknown_key_is_rejected_with_location() -> None:
    with pytest.raises(ConfigError, match=r"mutants\.budgte"):
        build_config({"mutants": {"budgte": 3}})


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown profile"):
        build_config({"run": {"profile": "turbo"}})


def test_invalid_value_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"flaky\.max_ratio"):
        build_config({"flaky": {"max_ratio": 1.5}})


def test_mutant_thresholds_must_be_ordered() -> None:
    with pytest.raises(ConfigError, match="unproven_below"):
        build_config({"mutants": {"unproven_below": 0.9, "proven_at_least": 0.8}})


def test_invalid_toml_is_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        parse_toml("[run\nprofile = 'fast'")


def test_example_config_parses_and_matches_defaults() -> None:
    cfg = load_config(REPO_ROOT / "counterproof.example.toml")
    defaults = build_config()
    assert cfg.llm == defaults.llm
    assert cfg.mutants == defaults.mutants
    assert cfg.impact == defaults.impact


def test_env_pass_uses_on_disk_key_name() -> None:
    cfg = build_config({"env": {"pass": ["DATABASE_URL_TEST"]}})
    assert cfg.env.pass_ == ["DATABASE_URL_TEST"]
    assert cfg.to_toml_dict()["env"] == {"pass": ["DATABASE_URL_TEST"]}
    # The serialised form must load back to the same config.
    assert build_config(cfg.to_toml_dict()) == cfg


def test_config_sha256_is_stable_and_sensitive() -> None:
    a = build_config({"run": {"workers": 2}})
    b = build_config({"run": {"workers": 2}})
    c = build_config({"run": {"workers": 2}, "mutants": {"budget": 7}})
    assert a.sha256() == b.sha256()
    assert a.sha256() != c.sha256()
    assert len(a.sha256()) == 64


def test_oracle_disabled_removes_blind_oracle_probe() -> None:
    cfg = build_config({"oracle": {"enabled": False}})
    assert "blind_oracle" not in cfg.enabled_probes
    assert "mutants" in cfg.enabled_probes


@pytest.mark.parametrize(("cpus", "expected"), [(None, 1), (1, 1), (2, 1), (8, 4), (64, 4)])
def test_fr_ws_03_default_workers(
    monkeypatch: pytest.MonkeyPatch, cpus: int | None, expected: int
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: cpus)
    assert default_workers() == expected


def test_load_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "counterproof.toml") == build_config()


def test_load_config_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "counterproof.toml"
    path.write_text('[run]\nprofile = "paranoid"\n[flaky]\nknown = ["t::x"]\n', encoding="utf-8")
    cfg = load_config(path, {"run": {"mode": "coach"}})
    assert isinstance(cfg, Config)
    assert cfg.run.profile == "paranoid"
    assert cfg.run.mode == "coach"
    assert cfg.flaky.known == ["t::x"]
    assert cfg.mutants.budget == 16
