"""Base-versus-head comparison of Python test files (FR-IN-01 to FR-IN-06)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from counterproof.models import Finding, Severity
from counterproof.probes.pyast import TestFile, TestFunction, analyse_test_file

SNIPPET_CHARS: Final = 600
SEVERITY: Final[dict[str, Severity]] = {
    "FR-IN-01": "high",
    "FR-IN-02": "high",
    "FR-IN-03": "high",
    "FR-IN-04": "medium",
    "FR-IN-05": "high",
    "FR-IN-06": "medium",
    "FR-IN-07": "high",
    "FR-IN-08": "high",
    "FR-IN-09": "medium",
}


@dataclass(frozen=True)
class TestFilePair:
    """One changed test file: ``path`` in head (or base, if deleted)."""

    __test__ = False

    path: str
    base_path: str | None
    base_text: str | None
    head_text: str | None


@dataclass(frozen=True)
class ChangedSource:
    """A changed source file as FR-IN-06 sees it."""

    path: str
    module: str
    definitions: frozenset[str]


def _snippet(fn: TestFunction | None) -> str | None:
    return fn.code[:SNIPPET_CHARS] if fn else None


def finding(
    rule_id: str,
    message: str,
    *,
    file: str | None,
    line: int | None,
    base: TestFunction | None = None,
    head: TestFunction | None = None,
    base_snippet: str | None = None,
    head_snippet: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=SEVERITY[rule_id],
        message=message,
        file=file,
        line=line,
        base_snippet=base_snippet if base_snippet is not None else _snippet(base),
        head_snippet=head_snippet if head_snippet is not None else _snippet(head),
    )


def _patches_unit_under_test(target: str, sources: list[ChangedSource]) -> str | None:
    """FR-IN-06: the patched symbol is defined in a changed source file."""
    for src in sources:
        if target.startswith(src.module + "."):
            symbol = target[len(src.module) + 1 :].split(".", 1)[0]
            if symbol in src.definitions:
                return src.path
        head, _, symbol = target.rpartition(".")
        if symbol in src.definitions and head.rsplit(".", 1)[-1] == src.module.rsplit(".", 1)[-1]:
            return src.path
    return None


def _covers(nodeid: str, source: str, covering_by_file: Mapping[str, set[str]] | None) -> bool:
    if covering_by_file is None:
        return True  # no coverage map: assume the test exercises the file it patches
    return nodeid in covering_by_file.get(source, set())


def _new_patch_findings(
    nodeid: str,
    path: str,
    *,
    base: TestFunction | None,
    head: TestFunction,
    sources: list[ChangedSource],
    covering_by_file: Mapping[str, set[str]] | None,
) -> list[Finding]:
    old = Counter(t for _, t in base.patch_targets) if base else Counter()
    results: list[Finding] = []
    for line, target in head.patch_targets:
        if old[target] > 0:
            old[target] -= 1
            continue
        source = _patches_unit_under_test(target, sources)
        if source and _covers(nodeid, source, covering_by_file):
            results.append(
                finding(
                    "FR-IN-06",
                    f"{nodeid} newly mocks `{target}`, defined in the changed file {source}",
                    file=path,
                    line=line,
                    base=base,
                    head=head,
                )
            )
    return results


def _compare_existing(
    nodeid: str, path: str, base: TestFunction, head: TestFunction
) -> list[Finding]:
    results: list[Finding] = []
    if head.asserts < base.asserts:
        results.append(
            finding(
                "FR-IN-01",
                f"{nodeid} has {head.asserts} assertion(s), down from {base.asserts}",
                file=path,
                line=head.line,
                base=base,
                head=head,
            )
        )
    added_skips = head.skips - base.skips
    if added_skips:
        results.append(
            finding(
                "FR-IN-03",
                f"{nodeid} gained skip/xfail: {', '.join(sorted(added_skips))}",
                file=path,
                line=head.line,
                base=base,
                head=head,
            )
        )
    base_neutral = Counter(reason for _, reason in base.neutralised)
    for line, reason in head.neutralised:
        if base_neutral[reason] > 0:
            base_neutral[reason] -= 1
            continue
        results.append(
            finding("FR-IN-05", f"{nodeid}: {reason}", file=path, line=line, base=base, head=head)
        )
    loosened: list[str] = []
    if head.eq_asserts < base.eq_asserts and head.truthy_or_in_asserts > base.truthy_or_in_asserts:
        loosened.append("an equality assertion became a truthiness or `in` check")
    if head.approx_tolerances and max(head.approx_tolerances) > max(
        base.approx_tolerances, default=0
    ):
        loosened.append("pytest.approx tolerance increased")
    if head.raises_without_match > base.raises_without_match:
        loosened.append("pytest.raises lost its `match=` check")
    if loosened:
        results.append(
            finding(
                "FR-IN-04",
                f"{nodeid}: {'; '.join(loosened)}",
                file=path,
                line=head.line,
                base=base,
                head=head,
            )
        )
    return results


def _compare_new(
    nodeid: str, path: str, head: TestFunction, base_module_skips: frozenset[str]
) -> list[Finding]:
    results: list[Finding] = []
    skips = head.skips - base_module_skips
    if skips:
        results.append(
            finding(
                "FR-IN-03",
                f"new test {nodeid} is skipped/xfailed: {', '.join(sorted(skips))}",
                file=path,
                line=head.line,
                head=head,
            )
        )
    for line, reason in head.neutralised:
        results.append(
            finding("FR-IN-05", f"new test {nodeid}: {reason}", file=path, line=line, head=head)
        )
    return results


def compare_test_files(
    pairs: list[TestFilePair],
    sources: list[ChangedSource],
    covering_by_file: Mapping[str, set[str]] | None,
) -> list[Finding]:
    """FR-IN-01 to FR-IN-06 over every changed Python test file."""
    base_tests: dict[str, tuple[str, TestFunction]] = {}
    head_tests: dict[str, tuple[str, TestFunction]] = {}
    base_module_skips: dict[str, frozenset[str]] = {}
    results: list[Finding] = []

    for pair in pairs:
        base_file: TestFile | None = (
            analyse_test_file(pair.base_text) if pair.base_text is not None else None
        )
        head_file: TestFile | None = (
            analyse_test_file(pair.head_text) if pair.head_text is not None else None
        )
        if base_file and pair.base_path:
            for suffix, fn in base_file.tests.items():
                base_tests[f"{pair.base_path}::{suffix}"] = (pair.base_path, fn)
        if head_file:
            base_module_skips[pair.path] = base_file.module_skips if base_file else frozenset()
            for suffix, fn in head_file.tests.items():
                head_tests[f"{pair.path}::{suffix}"] = (pair.path, fn)

    # A renamed test file keeps its tests: match base ids through the rename.
    renames = {p.base_path: p.path for p in pairs if p.base_path and p.base_path != p.path}
    head_bodies = {fn.body_hash for _, fn in head_tests.values()}

    def head_id(base_id: str) -> str:
        file, _, suffix = base_id.partition("::")
        return f"{renames.get(file, file)}::{suffix}"

    for nodeid, (base_path, removed) in sorted(base_tests.items()):
        if head_id(nodeid) in head_tests:
            continue
        # FR-IN-02: deleted or renamed away, unless an identical body exists in head.
        if removed.body_hash in head_bodies:
            continue
        results.append(
            finding(
                "FR-IN-02",
                f"test {nodeid} was deleted or renamed away with a different body",
                file=base_path,
                line=removed.line,
                base=removed,
            )
        )

    base_by_head_id = {head_id(nid): fn for nid, (_, fn) in base_tests.items()}
    for nodeid, (path, head_fn) in sorted(head_tests.items()):
        base_fn = base_by_head_id.get(nodeid)
        if base_fn is not None:
            results += _compare_existing(nodeid, path, base_fn, head_fn)
        else:
            skips = base_module_skips.get(path, frozenset())
            results += _compare_new(nodeid, path, head_fn, skips)
        results += _new_patch_findings(
            nodeid,
            path,
            base=base_fn,
            head=head_fn,
            sources=sources,
            covering_by_file=covering_by_file,
        )
    return results


def changed_test_ids(pairs: list[TestFilePair]) -> list[str]:
    """FR-RG-01: head test ids that were added or whose body changed."""
    changed: list[str] = []
    for pair in pairs:
        if pair.head_text is None:
            continue
        head_file = analyse_test_file(pair.head_text)
        if head_file is None:
            continue
        base_file = analyse_test_file(pair.base_text) if pair.base_text is not None else None
        base_tests = base_file.tests if base_file else {}
        for suffix, fn in head_file.tests.items():
            old = base_tests.get(suffix)
            if old is None or old.body_hash != fn.body_hash or old.skips != fn.skips:
                changed.append(f"{pair.path}::{suffix}")
    return sorted(changed)
