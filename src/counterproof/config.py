"""Configuration model and loader for ``counterproof.toml`` (PRD section 12).

Precedence, lowest to highest: built-in defaults, profile defaults (section 5.4), the config
file, explicit CLI overrides. Unknown keys are rejected so that a typo cannot silently disable
a check.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from counterproof.errors import ConfigError
from counterproof.evidence.hashing import canonical_json, sha256_bytes
from counterproof.models import ProbeName

Profile = Literal["fast", "full", "paranoid"]
Mode = Literal["audit", "coach"]
SandboxKind = Literal["none", "docker"]
ProviderName = Literal["anthropic", "openai", "deepseek", "ollama", "none"]

CONFIG_FILENAME: Final = "counterproof.toml"

ALL_PROBES: Final[tuple[ProbeName, ...]] = (
    "red_green",
    "integrity",
    "mutants",
    "blind_oracle",
    "claim_audit",
)


@dataclass(frozen=True)
class ProfileSpec:
    """Static description of a run profile (PRD section 5.4)."""

    name: Profile
    probes: tuple[ProbeName, ...]
    mutant_budget: int
    baseline_runs: int
    oracle_models: int
    mutant_models: int
    target_seconds: int


PROFILES: Final[dict[Profile, ProfileSpec]] = {
    "fast": ProfileSpec(
        name="fast",
        probes=("red_green", "integrity", "mutants", "claim_audit"),
        mutant_budget=4,
        baseline_runs=2,
        oracle_models=0,
        mutant_models=1,
        target_seconds=90,
    ),
    "full": ProfileSpec(
        name="full",
        probes=ALL_PROBES,
        mutant_budget=8,
        baseline_runs=3,
        oracle_models=1,
        mutant_models=1,
        target_seconds=300,
    ),
    "paranoid": ProfileSpec(
        name="paranoid",
        probes=ALL_PROBES,
        mutant_budget=16,
        baseline_runs=5,
        oracle_models=2,
        mutant_models=2,
        target_seconds=900,
    ),
}


def default_workers() -> int:
    """FR-WS-03: ``min(4, cpu_count // 2)``, never below one."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunConfig(_Section):
    profile: Profile = "full"
    mode: Mode = "audit"
    workers: int = Field(default_factory=default_workers, ge=1, le=64)
    sandbox: SandboxKind = "none"
    short_circuit: bool = True


class LLMConfig(_Section):
    # Local-first default (PRD goal 4): no provider until the user opts in.
    provider: ProviderName = "none"
    # "auto" picks the provider's default model for each role (llm/provider.py).
    mutant_model: str = "auto"
    judge_model: str = "auto"
    oracle_model: str = "auto"
    budget_usd: float = Field(default=0.30, ge=0.0)
    budget_tokens: int = Field(default=400_000, ge=0)
    # Wall-clock budget for LLM work; unset means the profile's target time (section 5.4).
    budget_seconds: float | None = Field(default=None, gt=0.0)
    # DeepSeek only: thinking mode (off by default: cheaper, faster, honours temperature).
    thinking: bool = False
    base_url: str | None = None


class ImpactConfig(_Section):
    max_tests: int = Field(default=400, ge=1)
    baseline_runs: int = Field(default=3, ge=1, le=20)
    # Hard timeout for one baseline or coverage run of the selected tests (FR-WS-06).
    run_timeout_s: float = Field(default=600.0, gt=0.0)


class FlakyConfig(_Section):
    max_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    known: list[str] = Field(default_factory=list)


class MutantsConfig(_Section):
    budget: int = Field(default=8, ge=0, le=64)
    llm_budget: int = Field(default=6, ge=0, le=64)
    unproven_below: float = Field(default=0.5, ge=0.0, le=1.0)
    proven_at_least: float = Field(default=0.8, ge=0.0, le=1.0)
    fuzz: bool = True

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> MutantsConfig:
        if self.unproven_below > self.proven_at_least:
            msg = "mutants.unproven_below must not exceed mutants.proven_at_least"
            raise ValueError(msg)
        return self


class OracleConfig(_Section):
    enabled: bool = True
    k: int = Field(default=6, ge=1, le=32)


class PathsConfig(_Section):
    exclude: list[str] = Field(default_factory=list)


class EnvConfig(_Section):
    # FR-WS-04: variables explicitly allowed through to child processes.
    pass_: list[str] = Field(default_factory=list, alias="pass")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Config(_Section):
    run: RunConfig = Field(default_factory=RunConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    impact: ImpactConfig = Field(default_factory=ImpactConfig)
    flaky: FlakyConfig = Field(default_factory=FlakyConfig)
    mutants: MutantsConfig = Field(default_factory=MutantsConfig)
    oracle: OracleConfig = Field(default_factory=OracleConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)

    @property
    def profile_spec(self) -> ProfileSpec:
        return PROFILES[self.run.profile]

    @property
    def enabled_probes(self) -> tuple[ProbeName, ...]:
        """Probes for this profile; the blind oracle also honours ``oracle.enabled``."""
        return tuple(
            p for p in self.profile_spec.probes if p != "blind_oracle" or self.oracle.enabled
        )

    def to_toml_dict(self) -> dict[str, Any]:
        """JSON-safe dict using the on-disk key names (``env.pass``, not ``pass_``)."""
        return self.model_dump(mode="json", by_alias=True)

    def sha256(self) -> str:
        """Hash of the effective configuration, recorded as ``config_sha256``."""
        return sha256_bytes(canonical_json(self.to_toml_dict()))


def _profile_defaults(profile: Profile) -> dict[str, Any]:
    spec = PROFILES[profile]
    return {
        "run": {"profile": profile},
        "mutants": {"budget": spec.mutant_budget},
        "impact": {"baseline_runs": spec.baseline_runs},
    }


def _deep_merge(base: Mapping[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in top.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def parse_toml(text: str, *, source: str = CONFIG_FILENAME) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: invalid TOML: {exc}") from exc


def _format_validation_error(exc: ValidationError, source: str) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return f"{source}: " + "; ".join(parts)


def build_config(
    file_data: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
    *,
    source: str = CONFIG_FILENAME,
) -> Config:
    """Merge defaults, profile defaults, file data and CLI overrides, then validate."""
    file_data = file_data or {}
    overrides = overrides or {}
    profile: Any = _get(overrides, "run", "profile") or _get(file_data, "run", "profile") or "full"
    if profile not in PROFILES:
        raise ConfigError(f"{source}: run.profile: unknown profile {profile!r}")
    merged = _deep_merge(_deep_merge(_profile_defaults(profile), file_data), overrides)
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, source)) from exc


def load_config(path: Path | None, overrides: Mapping[str, Any] | None = None) -> Config:
    """Load ``path`` if given and present, otherwise defaults, then apply overrides."""
    if path is None or not path.is_file():
        return build_config({}, overrides, source="<defaults>")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    return build_config(parse_toml(text, source=str(path)), overrides, source=str(path))


def _get(data: Mapping[str, Any], section: str, key: str) -> Any:
    sub = data.get(section)
    return sub.get(key) if isinstance(sub, Mapping) else None
