"""Pydantic output schemas for the LLM roles (PRD section 9).

Schemas are tolerant of extra fields (models add them) but strict on the fields we use.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ForgeMutant(_Tolerant):
    id: str = ""
    file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str
    bug_class: str
    plausibility: int = Field(ge=1, le=5)
    rationale: str = ""


class ForgeOutput(_Tolerant):
    mutants: list[ForgeMutant] = Field(max_length=32)


class JudgeOutput(_Tolerant):
    equivalent: bool
    confidence: float = Field(ge=0.0, le=1.0)
    distinguishing_input: str | None = None


class OracleTest(_Tolerant):
    name: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1)
    code: str = Field(min_length=1)


class OracleOutput(_Tolerant):
    tests: list[OracleTest] = Field(max_length=32)
    imports: list[str] = Field(default_factory=list, max_length=40)


class TriageOutput(_Tolerant):
    label: Literal["oracle_wrong", "ambiguous", "likely_bug"]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = ""
