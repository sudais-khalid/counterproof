"""Secret redaction for every prompt (PRD section 15.2).

Known secret formats are replaced first; then any long, high-entropy token mixing letters and
digits. Identifiers (``percent_discount_with_cap``) are long but low-entropy and survive.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Final

PLACEHOLDER: Final = "[REDACTED]"
KNOWN: Final = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
ASSIGNED: Final = re.compile(
    r"(?i)\b([\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d)[\w-]*)\s*([:=])\s*(['\"])([^'\"\n]{8,})\3"
)
CANDIDATE: Final = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
MIN_ENTROPY: Final = 4.0

# Files that must never be sent to a provider (PRD 15.2).
NEVER_SEND: Final = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "credentials*",
)


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def _high_entropy(token: str) -> bool:
    return (
        any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
        and _entropy(token) >= MIN_ENTROPY
    )


def redact(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, number_of_redactions)``."""
    count = 0

    def sub(pattern: re.Pattern[str], replacement: str, value: str) -> str:
        nonlocal count
        result, n = pattern.subn(replacement, value)
        count += n
        return result

    for pattern in KNOWN:
        text = sub(pattern, PLACEHOLDER, text)
    text = sub(ASSIGNED, rf"\1 \2 \3{PLACEHOLDER}\3", text)

    def entropy_sub(match: re.Match[str]) -> str:
        nonlocal count
        if _high_entropy(match.group(0)):
            count += 1
            return PLACEHOLDER
        return match.group(0)

    return CANDIDATE.sub(entropy_sub, text), count


def may_send(path: str, excluded: Sequence[str]) -> bool:
    """False for secrets, lockfiles and ``paths.exclude`` matches."""
    name = PurePosixPath(path).name
    if any(fnmatchcase(name, pattern) for pattern in NEVER_SEND):
        return False
    return not any(fnmatchcase(path, pattern) for pattern in excluded)
