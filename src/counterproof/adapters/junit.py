"""JUnit XML parsing (pytest ``junit_family=xunit2``; Vitest and Jest later).

pytest identifies a case by ``classname`` (dotted module path, plus classes) and ``name``.
We map those back to node ids by computing the expected key for every requested node id, so
parametrised ids and nested classes round-trip exactly. Collection errors appear as a case
with an empty classname whose ``name`` is the dotted module path.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

CaseOutcome = Literal["pass", "fail", "error", "skip"]
COLLECTION_FAILURE: Final = "collection failure"
MAX_TEXT: Final = 4000
_TAG_OUTCOME: Final[dict[str, CaseOutcome]] = {
    "failure": "fail",
    "error": "error",
    "skipped": "skip",
}


@dataclass(frozen=True)
class JUnitCase:
    classname: str
    name: str
    outcome: CaseOutcome
    message: str
    text: str
    time_s: float

    @property
    def is_collection_error(self) -> bool:
        return self.outcome == "error" and (
            self.message == COLLECTION_FAILURE or (not self.classname and "." in self.name)
        )


def parse_junit(path: Path) -> list[JUnitCase]:
    """Parse a junit file; a missing or empty file yields no cases."""
    if not path.is_file() or path.stat().st_size == 0:
        return []
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - runner-written file in our run dir
    except ET.ParseError:
        return []
    cases: list[JUnitCase] = []
    for case in root.iter("testcase"):
        outcome: CaseOutcome = "pass"
        message = text = ""
        for child in case:
            mapped = _TAG_OUTCOME.get(child.tag)
            if mapped is None:
                continue
            outcome = mapped
            message = child.get("message", "") or ""
            text = (child.text or "")[:MAX_TEXT]
            if mapped != "skip":
                break
        try:
            time_s = float(case.get("time", "0") or 0)
        except ValueError:
            time_s = 0.0
        cases.append(
            JUnitCase(
                classname=case.get("classname", "") or "",
                name=case.get("name", "") or "",
                outcome=outcome,
                message=message,
                text=text,
                time_s=time_s,
            )
        )
    return cases


def module_of(file: str) -> str:
    """``tests/test_a.py`` -> ``tests.test_a``."""
    pure = PurePosixPath(file)
    return ".".join([*pure.parent.parts, pure.stem]) if pure.parent.parts else pure.stem


def junit_key(nodeid: str) -> tuple[str, str]:
    """``tests/test_a.py::TestK::test_p[1]`` -> ``("tests.test_a.TestK", "test_p[1]")``."""
    file, _, rest = nodeid.partition("::")
    parts = rest.split("::") if rest else []
    module = module_of(file)
    if not parts:
        return "", module
    return ".".join([module, *parts[:-1]]), parts[-1]
