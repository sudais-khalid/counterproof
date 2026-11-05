"""Intent inference (FR-CR-05).

Rules first: a change touching only tests is ``test_only``; one touching no code at all is
``non_code``. For source changes the task text decides between bugfix, feature and refactor
by keyword. When nothing matches, the strictest label (``bugfix``, which requires red-green
evidence) is used; the LLM classifier for ambiguous cases arrives with the LLM layer (M4).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from counterproof.models import ChangedFile, Intent, IntentSource

_REFACTOR: Final = re.compile(
    r"\b(refactor\w*|renam\w*|restructur\w*|clean[\s-]?up|simplif\w*|extract\w*|"
    r"reorgani[sz]\w*|tidy)\b",
    re.IGNORECASE,
)
_NO_BEHAVIOUR_CHANGE: Final = re.compile(
    r"\b(no|without) (functional|behaviou?r(al)?) changes?\b", re.IGNORECASE
)
_BUGFIX: Final = re.compile(
    r"\b(fix\w*|bug\w*|broken|wrong|incorrect\w*|error\w*|crash\w*|fail\w*|regression|"
    r"should (be|return|give|not)|instead of|expected)\b",
    re.IGNORECASE,
)
_FEATURE: Final = re.compile(
    r"\b(add|adds|adding|implement\w*|support\w*|introduc\w*|new|feature|allow\w*|enabl\w*)\b",
    re.IGNORECASE,
)
_CODE_ROLES: Final = frozenset({"source", "test"})


def infer_intent(
    files: Sequence[ChangedFile],
    task_text: str | None,
    user_intent: Intent | None = None,
) -> tuple[Intent, IntentSource]:
    if user_intent is not None:
        return user_intent, "user"
    roles = {f.role for f in files if not f.binary}
    if not roles & _CODE_ROLES:
        return "non_code", "rule"
    if "source" not in roles:
        return "test_only", "rule"
    text = task_text or ""
    if _NO_BEHAVIOUR_CHANGE.search(text):
        return "refactor", "rule"
    if _BUGFIX.search(text):
        return "bugfix", "rule"
    if _REFACTOR.search(text):
        return "refactor", "rule"
    if _FEATURE.search(text):
        return "feature", "rule"
    return "bugfix", "rule"
