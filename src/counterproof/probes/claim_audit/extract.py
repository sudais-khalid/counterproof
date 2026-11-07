"""Claims in the agent's final message (FR-CA-02), rule-based.

Three kinds: test claims ("all 42 tests pass", "the suite is green"), completion claims
("fixed", "done") and scope claims ("the full suite", a named test file). A sentence that
negates or hedges ("not all tests pass", "should pass once ...") is not a pass claim; hedged
test sentences are reported as ambiguous instead. LLM extraction for ambiguous phrasing is
not implemented yet: ambiguous sentences only add a no-penalty signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

_FLAGS: Final = re.I
_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKUP: Final = re.compile(r"[*`#>]+")
# "0 failed", "no failures", "without errors" are not negations.
_HARMLESS: Final = re.compile(
    r"\b(?:0|no|zero)\s+(?:tests?\s+)?(?:failed|failures?|failing|errors?)\b"
    r"|\bwithout\s+(?:any\s+)?(?:failures?|errors?|warnings?|regressions?)\b",
    _FLAGS,
)
_NEGATION: Final = re.compile(
    r"\b(?:not|never|unable|cannot|can't|couldn't|didn't|don't|doesn't|won't|isn't|aren't|"
    r"wasn't|weren't|haven't|hasn't)\b"
    r"|\b[1-9]\d*\s+(?:tests?\s+)?(?:failed|failures?|failing|errors?)\b"
    r"|\btests?\s+(?:still\s+|now\s+)?(?:fail|fails|failed|failing|break|broke|broken)\b"
    r"|\b(?:still|now)\s+fail",
    _FLAGS,
)
_HEDGE: Final = re.compile(
    r"\b(?:should|would|will|might|may|could|expected\s+to|once|if|when\s+you)\b", _FLAGS
)
_TEST_WORD: Final = re.compile(
    r"\btests?\b|\bsuite\b|\bpytest\b|\bgreen\b|\bspecs?\b|\b\d+\s+passed\b", _FLAGS
)
_PASS: Final = (
    re.compile(
        r"\b(?:all|every|each)\b[^.!?\n]{0,40}?\btests?\b[^.!?\n]{0,25}?"
        r"\b(?:pass|passes|passed|passing|succeed|succeeds|succeeded|green|ok)\b",
        _FLAGS,
    ),
    re.compile(
        r"\btests?\b[^.!?\n]{0,25}?\b(?:pass|passes|passed|passing|succeed\w*|green)\b", _FLAGS
    ),
    re.compile(r"\b\d+\s*(?:/\s*\d+\s*)?(?:\w+\s+){0,2}?(?:tests?\s+)?passed\b", _FLAGS),
    re.compile(
        r"\b(?:suite|pytest|jest|vitest|build)\b[^.!?\n]{0,20}?"
        r"\b(?:green|passes|passed|passing|clean)\b",
        _FLAGS,
    ),
    re.compile(r"\ball\s+green\b", _FLAGS),
    re.compile(
        r"[\w./\\-]*test[\w./\\-]*\.(?:py|[jt]sx?|mjs|cjs)\s+(?:now\s+|all\s+|still\s+)?"
        r"(?:pass|passes|passed|is\s+green)\b",
        _FLAGS,
    ),
)
_COUNTS: Final = (
    re.compile(r"\b(\d+)\s*/\s*\d+\s+(?:\w+\s+)?(?:tests?\s+)?pass", _FLAGS),
    re.compile(r"\ball\s+(\d+)\s+(?:\w+\s+)?tests?\b", _FLAGS),
    re.compile(r"\b(\d+)\s+(?:\w+\s+)?tests?\s+(?:now\s+|all\s+)?pass", _FLAGS),
    re.compile(r"\b(\d+)\s+passed\b", _FLAGS),
)
_FULL_SCOPE: Final = re.compile(
    r"\b(?:full|entire|whole|complete)\s+(?:test\s+)?suite\b|\ball\s+(?:the\s+)?(?:\d+\s+)?tests\b"
    r"|\bevery\s+test\b|\btest\s+suite\b",
    _FLAGS,
)
_TEST_FILE: Final = re.compile(r"[\w./\\-]*test[\w./\\-]*\.(?:py|[jt]sx?|mjs|cjs)\b", _FLAGS)
_COMPLETION: Final = re.compile(
    r"\b(?:done|fixed|implemented|completed?|resolved|finished|works?\s+now)\b", _FLAGS
)


@dataclass(frozen=True)
class Claims:
    pass_claim: bool = False
    claimed_passed: int | None = None
    full_suite: bool = False
    files: list[str] = field(default_factory=list)
    completion: bool = False
    quotes: list[str] = field(default_factory=list)
    """The sentences that made a test claim (for the certificate)."""
    ambiguous: list[str] = field(default_factory=list)


def sentences(text: str) -> list[str]:
    out = []
    for raw in _SENTENCE.split(text):
        clean = " ".join(_MARKUP.sub("", raw).split())
        if clean:
            out.append(clean)
    return out


def _claimed_count(sentence: str) -> int | None:
    for pattern in _COUNTS:
        match = pattern.search(sentence)
        if match:
            return int(match.group(1))
    return None


def extract_claims(message: str | None) -> Claims:
    if not message:
        return Claims()
    pass_claim = False
    count: int | None = None
    full = False
    files: list[str] = []
    quotes: list[str] = []
    ambiguous: list[str] = []
    completion = False
    for sentence in sentences(message):
        completion = completion or bool(_COMPLETION.search(sentence))
        if not _TEST_WORD.search(sentence):
            continue
        claims_pass = any(p.search(sentence) for p in _PASS)
        if not claims_pass:
            continue
        stripped = _HARMLESS.sub(" ", sentence)
        if _NEGATION.search(stripped):
            continue
        if _HEDGE.search(stripped):
            ambiguous.append(sentence[:200])
            continue
        pass_claim = True
        quotes.append(sentence[:200])
        if count is None:
            count = _claimed_count(stripped)
        full = full or bool(_FULL_SCOPE.search(stripped))
        files += [f for f in _TEST_FILE.findall(sentence) if f not in files]
    return Claims(
        pass_claim=pass_claim,
        claimed_passed=count,
        full_suite=full,
        files=files,
        completion=completion,
        quotes=quotes,
        ambiguous=ambiguous,
    )
