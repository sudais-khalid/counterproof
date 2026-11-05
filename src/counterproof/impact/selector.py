"""ImpactSelector: changed lines to test ids (FR-IS-01, FR-IS-02)."""

from __future__ import annotations

from collections.abc import Sequence

from counterproof.adapters.base import CoverageMap
from counterproof.change.diff import changed_new_lines
from counterproof.models import ChangeSet, Selection, TestId


def changed_source_lines(change: ChangeSet) -> dict[str, set[int]]:
    """Head line numbers touched in each surviving source file."""
    live_sources = {f.path for f in change.files if f.role == "source" and f.status != "deleted"}
    lines: dict[str, set[int]] = {}
    for hunk in change.hunks:
        if hunk.path in live_sources:
            lines.setdefault(hunk.path, set()).update(changed_new_lines(hunk))
    return lines


def select_tests(
    change: ChangeSet,
    all_tests: Sequence[TestId],
    coverage: CoverageMap | None,
    related: Sequence[TestId],
    *,
    max_tests: int,
) -> Selection:
    """FR-IS-01: covering tests, plus every test in changed test files, plus related tests.

    Without a coverage map, related tests (name heuristics, FR-IS-02) stand in for coverage.
    When the union exceeds ``max_tests`` the selection falls back to whole test files that
    contain covering tests or were changed.
    """
    known = {t.nodeid: t for t in all_tests}
    changed_test_files = {
        f.path for f in change.files if f.role == "test" and f.status != "deleted"
    }
    in_changed = sorted(n for n, t in known.items() if t.file in changed_test_files)

    covering: set[str] = set()
    if coverage is not None:
        for path, lines in changed_source_lines(change).items():
            for line in lines:
                covering |= coverage.get((path, line), set())
        covering &= set(known)  # drop contexts for tests that no longer exist
    related_ids = sorted({t.nodeid for t in related} & set(known))

    union = sorted(covering | set(in_changed) | set(related_ids))
    selection = Selection(
        tests=union,
        covering=sorted(covering),
        in_changed_test_files=in_changed,
        related=related_ids,
        coverage_available=coverage is not None,
    )
    if len(union) <= max_tests:
        return selection
    files = sorted({known[n].file for n in covering | set(in_changed)} or changed_test_files)
    in_files = sorted(n for n, t in known.items() if t.file in set(files))
    return selection.model_copy(update={"tests": in_files, "capped": True, "file_targets": files})
