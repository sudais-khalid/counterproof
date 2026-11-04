"""Exception hierarchy. CLI entry points map these to the exit codes in PRD section 11."""

from __future__ import annotations


class CounterproofError(Exception):
    """Base class for expected, user-facing failures."""


class UsageError(CounterproofError):
    """Bad invocation: invalid flag combination, not a git repository, missing file."""


class ConfigError(UsageError):
    """counterproof.toml could not be parsed or failed validation."""


class GitError(CounterproofError):
    """A git command failed or timed out."""


class RunDirError(CounterproofError):
    """The run directory could not be created or would violate FR-WS-02."""


class EvidenceError(CounterproofError):
    """An artefact could not be written or sealed."""
