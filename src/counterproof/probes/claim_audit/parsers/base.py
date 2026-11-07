"""Agent-neutral view of a session transcript (FR-CA-01, FR-CA-03)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Command:
    """One shell command the agent ran, with what came back."""

    index: int
    """Position in the session (event order); ``after the last edit`` compares these."""
    line: int
    """1-based line of the transcript file, for evidence references."""
    command: str
    tool: str
    timestamp: str | None = None
    output: str | None = None
    exit_code: int | None = None
    """None when the result was never recorded or the exit code is unknown."""


@dataclass(frozen=True)
class FileEdit:
    index: int
    line: int
    path: str
    tool: str
    timestamp: str | None = None


@dataclass(frozen=True)
class Transcript:
    format: str
    commands: list[Command] = field(default_factory=list)
    edits: list[FileEdit] = field(default_factory=list)
    final_message: str | None = None
    final_message_line: int | None = None
    author_models: list[str] = field(default_factory=list)
    """Models that wrote assistant turns, least recently used first (FR-BO-07)."""

    @property
    def author_model(self) -> str | None:
        return self.author_models[-1] if self.author_models else None
