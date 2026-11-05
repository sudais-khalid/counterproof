"""Child-process environment scrubbing (FR-WS-04, PRD section 15.3).

Test processes get an allowlisted environment plus the variables named in config ``env.pass``.
Secret-looking names are dropped unless they are passed by their exact name, and no
``COUNTERPROOF_*`` variable ever reaches a child, so code under test cannot tell it is being
verified by looking at its environment.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from typing import Final

# Variables a test process legitimately needs on Linux, macOS and Windows.
ALLOWLIST: Final = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LANGUAGE",
        "TZ",
        "TMPDIR",
        "TEMP",
        "TMP",
        # Windows essentials: Python cannot even start without SYSTEMROOT.
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
)
ALLOWED_PREFIXES: Final = ("LC_",)

SECRET_PATTERNS: Final = (
    "*_KEY",
    "*_TOKEN",
    "*_SECRET",
    "AWS_*",
    "GITHUB_TOKEN",
    "*_PASSWORD",
    "*_PASSWD",
    "*_CREDENTIALS",
    "*_PAT",
)
INTERNAL_PREFIX: Final = "COUNTERPROOF_"


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pattern) for pattern in SECRET_PATTERNS)


def _is_pattern(entry: str) -> bool:
    return any(ch in entry for ch in "*?[")


def scrub_env(source: Mapping[str, str], passthrough: Iterable[str] = ()) -> dict[str, str]:
    """Return the environment a child process may see.

    ``passthrough`` entries are exact names or glob patterns. An exact name is passed even if
    it looks like a secret (the user asked for it explicitly); a pattern never lets a
    secret-looking name through.
    """
    exact = {p.upper() for p in passthrough if not _is_pattern(p)}
    patterns = [p.upper() for p in passthrough if _is_pattern(p)]
    result: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if upper.startswith(INTERNAL_PREFIX):
            continue
        if upper in exact:
            result[name] = value
            continue
        if is_secret_name(upper):
            continue
        if (
            upper in ALLOWLIST
            or upper.startswith(ALLOWED_PREFIXES)
            or any(fnmatch.fnmatchcase(upper, p) for p in patterns)
        ):
            result[name] = value
    return result


def dropped_names(source: Mapping[str, str], kept: Mapping[str, str]) -> list[str]:
    """Names removed by scrubbing, for the run log (never the values)."""
    return sorted(set(source) - set(kept))
