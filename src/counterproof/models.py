"""Core data models (PRD section 10).

Field names are normative. Every model is Pydantic v2 and is serialised to JSON in the run
directory. Models used for our own artefacts forbid unknown fields so that a typo or a
schema drift is caught at the boundary instead of silently ignored.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerdictName = Literal["PROVEN", "WEAK", "UNPROVEN", "REFUTED", "INCONCLUSIVE"]
Intent = Literal["bugfix", "feature", "refactor", "test_only", "non_code"]
IntentSource = Literal["user", "rule", "llm"]
FileRole = Literal["source", "test", "config", "docs", "other"]
FileStatus = Literal["added", "modified", "deleted", "renamed"]
TestOutcome = Literal["pass", "fail", "error", "skip", "timeout"]
# FR-AD-04: failure classification shared by every adapter.
FailureKind = Literal[
    "ASSERTION", "EXCEPTION_BEHAVIOURAL", "API_ABSENT", "COLLECTION", "TIMEOUT", "ENV"
]
Severity = Literal["low", "medium", "high"]
ProbeName = Literal["red_green", "integrity", "mutants", "blind_oracle", "claim_audit"]
ProbeStatus = Literal["ok", "skipped", "inconclusive"]
MutantSource = Literal["rule", "llm"]
MutantStatus = Literal[
    "invalid", "equivalent", "probably_equivalent", "killed", "survived", "timeout", "error"
]

# PRD section 5.2 and section 11: verdict to process exit code.
VERDICT_EXIT_CODES: Final[dict[VerdictName, int]] = {
    "PROVEN": 0,
    "WEAK": 10,
    "UNPROVEN": 20,
    "REFUTED": 30,
    "INCONCLUSIVE": 40,
}
EXIT_INTERNAL_ERROR: Final = 1
EXIT_USAGE_ERROR: Final = 2

WORKTREE: Final = "WORKTREE"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChangedFile(_Strict):
    path: str
    role: FileRole
    status: FileStatus
    old_path: str | None = None


class Hunk(_Strict):
    path: str
    old_start: int = Field(ge=0)
    old_len: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_len: int = Field(ge=0)
    lines: list[str] = Field(default_factory=list)


class ChangeSet(_Strict):
    repo_root: Path
    base_sha: str
    head_ref: str
    """A commit sha, or ``"WORKTREE"`` for uncommitted work."""
    head_snapshot_sha: str
    """Temporary commit object for WORKTREE heads; equals the head sha otherwise."""
    files: list[ChangedFile] = Field(default_factory=list)
    hunks: list[Hunk] = Field(default_factory=list)
    intent: Intent
    intent_source: IntentSource
    task_text: str | None = None
    transcript_path: Path | None = None
    flags: list[str] = Field(default_factory=list)


class TestId(_Strict):
    __test__ = False  # not a pytest test class

    adapter: str
    file: str
    name: str

    @property
    def nodeid(self) -> str:
        """``tests/test_cart.py::test_discount`` style identifier."""
        return f"{self.file}::{self.name}"


class FailureInfo(_Strict):
    kind: FailureKind
    message: str = ""
    traceback_excerpt: str = ""


class RunResult(_Strict):
    tests: dict[str, TestOutcome] = Field(default_factory=dict)
    failures: dict[str, FailureInfo] = Field(default_factory=dict)
    duration_s: float = Field(ge=0.0)
    command: list[str]
    exit_code: int
    junit_sha256: str


class Mutant(_Strict):
    id: str
    source: MutantSource
    operator_or_class: str
    file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    original: str
    replacement: str
    plausibility: int | None = Field(default=None, ge=1, le=5)
    status: MutantStatus
    killed_by: list[str] = Field(default_factory=list)
    hint: str | None = None


class Finding(_Strict):
    rule_id: str
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    base_snippet: str | None = None
    head_snippet: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    waived: bool = False
    waiver_reason: str | None = None


class Cost(_Strict):
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    usd: float = Field(default=0.0, ge=0.0)
    seconds: float = Field(default=0.0, ge=0.0)


class ProbeResult(_Strict):
    probe: ProbeName
    status: ProbeStatus
    signals: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    cost: Cost = Field(default_factory=Cost)


class Verdict(_Strict):
    verdict: VerdictName
    rule_id: str
    reasons: list[str] = Field(default_factory=list)
    exit_code: int
    confidence_note: str = ""

    @model_validator(mode="after")
    def _exit_code_matches_verdict(self) -> Verdict:
        expected = VERDICT_EXIT_CODES[self.verdict]
        if self.exit_code != expected:
            msg = f"exit_code {self.exit_code} does not match {self.verdict} ({expected})"
            raise ValueError(msg)
        return self

    @classmethod
    def make(
        cls,
        verdict: VerdictName,
        rule_id: str,
        reasons: list[str] | None = None,
        confidence_note: str = "",
    ) -> Verdict:
        """Build a verdict whose exit code is always consistent with section 5.2."""
        return cls(
            verdict=verdict,
            rule_id=rule_id,
            reasons=list(reasons or []),
            exit_code=VERDICT_EXIT_CODES[verdict],
            confidence_note=confidence_note,
        )


class RunManifest(_Strict):
    run_id: str
    tool_version: str
    created_at: datetime
    seed_commitment: str
    config_sha256: str
    change: ChangeSet
    probes: list[ProbeResult] = Field(default_factory=list)
    verdict: Verdict
    artefacts: dict[str, str] = Field(default_factory=dict)
    """Relative path inside the run directory -> sha256 of its bytes."""
