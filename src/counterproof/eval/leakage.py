"""Equivalent-mutant leakage (PRD section 3.3): the share of survivors *reported to users*
that a human judges equivalent. Target: <= 10%.

1. ``export``: collect every reported survivor from one or more runs into a review sheet
   (``survivors.md`` to read, ``survivors.csv`` to label).
2. A human fills the ``human_label`` column with ``equivalent`` or ``not_equivalent``.
3. ``score``: leakage = equivalent / labelled.

    python -m counterproof.eval.leakage export --out eval/leakage RUN_DIR [RUN_DIR ...]
    python -m counterproof.eval.leakage score eval/leakage/survivors.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from counterproof.report.bundle import load_bundle

COLUMNS: Final = (
    "sheet_id",
    "run_id",
    "task",
    "mutant_id",
    "location",
    "source",
    "bug_class",
    "plausibility",
    "original",
    "replacement",
    "tests_run",
    "judge",
    "hint",
    "seen_in_runs",
    "human_label",
    "notes",
)
LABELS: Final = frozenset({"equivalent", "not_equivalent"})


@dataclass(frozen=True)
class Score:
    labelled: int
    equivalent: int
    unlabelled: int

    @property
    def leakage(self) -> float | None:
        return self.equivalent / self.labelled if self.labelled else None


def export(run_dirs: list[Path], out_dir: Path) -> int:
    rows: list[dict[str, str]] = []
    seen: dict[tuple[str, str, int, str], dict[str, str]] = {}
    for run_dir in sorted(run_dirs):
        bundle = load_bundle(run_dir)
        task = (bundle.manifest.change.task_text or "").strip().splitlines()
        for m in bundle.mutants:
            if m.status != "survived":
                continue
            # The same mutant reported by several runs (e.g. two profiles) is labelled once.
            key = (task[0] if task else "", m.file, m.start_line, m.replacement)
            if key in seen:
                seen[key]["seen_in_runs"] = str(int(seen[key]["seen_in_runs"]) + 1)
                continue
            rows.append(
                {
                    "sheet_id": f"S{len(rows) + 1:03d}",
                    "run_id": bundle.manifest.run_id,
                    "task": task[0][:120] if task else "",
                    "mutant_id": m.id,
                    "location": f"{m.file}:{m.start_line}-{m.end_line}",
                    "source": m.source,
                    "bug_class": m.operator_or_class,
                    "plausibility": "" if m.plausibility is None else str(m.plausibility),
                    "original": m.original.rstrip("\n"),
                    "replacement": m.replacement.rstrip("\n"),
                    "tests_run": str(len(m.covering_tests)),
                    "judge": m.equivalence_note or "",
                    "hint": m.hint or "",
                    "seen_in_runs": "1",
                    "human_label": "",
                    "notes": "",
                }
            )
            seen[key] = rows[-1]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "survivors.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Surviving mutants for human review",
        "",
        "For each survivor decide: can ANY input make the original and the mutant behave",
        "differently? Put `equivalent` or `not_equivalent` in the `human_label` column of",
        "`survivors.csv` (same `sheet_id`).",
        "",
    ]
    for row in rows:
        lines += [
            f"## {row['sheet_id']}  {row['location']}  ({row['source']} / {row['bug_class']})",
            f"Run {row['run_id']} - task: {row['task']}",
            "",
            "```diff",
            *[f"- {line}" for line in row["original"].splitlines()],
            *[f"+ {line}" for line in row["replacement"].splitlines()],
            "```",
            f"Tests run against it: {row['tests_run']}. Judge: {row['judge'] or 'n/a'}.",
            f"Hint shown to the user: {row['hint'] or 'n/a'}",
            "",
        ]
    (out_dir / "survivors.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def score(csv_path: Path) -> Score:
    labelled = equivalent = unlabelled = 0
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("human_label") or "").strip().lower()
            if label in LABELS:
                labelled += 1
                equivalent += label == "equivalent"
            else:
                unlabelled += 1
    return Score(labelled=labelled, equivalent=equivalent, unlabelled=unlabelled)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m counterproof.eval.leakage")
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("--out", type=Path, required=True)
    ex.add_argument("runs", type=Path, nargs="+")
    sc = sub.add_parser("score")
    sc.add_argument("csv", type=Path)
    args = parser.parse_args(argv)
    if args.command == "export":
        print(f"exported {export(args.runs, args.out)} survivor(s) to {args.out}")
        return 0
    result = score(args.csv)
    shown = "n/a" if result.leakage is None else f"{result.leakage:.1%}"
    print(
        f"labelled {result.labelled}, equivalent {result.equivalent}, "
        f"unlabelled {result.unlabelled}: leakage {shown} (target <= 10%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
