"""The published evaluation table (PRD section 17.2), rendered from an :class:`EvalReport`.

Only verdict-derived numbers go into the comparison tables, so re-running the same corpus
and seed reproduces them exactly; runtime and cost are reported separately.
"""

from __future__ import annotations

from typing import Any

from counterproof.eval.harness import ABLATIONS, EvalReport

BASELINE_NAMES = {
    "B0": "B0 green tests only",
    "B1": "B1 claim audit only",
    "B2": "B2 diff-scoped classic mutation",
}
ABLATION_NAMES = {
    "claim_audit": "without claim audit (P5)",
    "integrity": "without integrity (P2)",
    "red_green": "without red-green (P1)",
    "mutants": "without mutants (P3)",
    "llm_mutants": "rule mutants only (no LLM mutants)",
    "blind_oracle": "without blind oracle (P4)",
}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _frac(hits: int, total: int) -> str:
    return f"{hits}/{total}" if total else "n/a"


def render_table(report: EvalReport) -> str:
    out = [
        f"# Counterproof evaluation: {report.corpus}",
        "",
        report.description.strip(),
        "",
        f"Counterproof {report.tool_version}, seed `{report.seed}`, generated {report.created_at}.",
        "Flagged (strict) = UNPROVEN or REFUTED (PRD 3.3); flagged (lenient) also counts WEAK.",
        "",
    ]
    summary: dict[str, Any] = report.summary
    out += [
        "## Headline",
        "",
        "| Configuration | Gamed recall, strict | Gamed recall, lenient | Honest false alarms, strict | Honest false alarms, lenient | Cases |",
        "|---|---|---|---|---|---|",
    ]
    for config, block in summary.items():
        out.append(
            f"| Counterproof ({config}) | {_pct(block['recall_strict'])} | "
            f"{_pct(block['recall_lenient'])} | {_pct(block['false_alarm_strict'])} | "
            f"{_pct(block['false_alarm_lenient'])} | {block['gamed']} gamed, {block['honest']} honest |"
        )
    first = next(iter(summary.values()), None)
    if first is not None:
        for name, title in BASELINE_NAMES.items():
            if name not in report.baselines:
                continue
            b = first["baselines"][name]
            note = (
                ""
                if b["applicable"] == first["cases"]
                else f" (applies to {b['applicable']} of {first['cases']} cases)"
            )
            out.append(
                f"| {title}{note} | {_pct(b['recall'])} | {_pct(b['recall'])} | "
                f"{_pct(b['false_alarm'])} | {_pct(b['false_alarm'])} | |"
            )
    for config, block in summary.items():
        out += [
            "",
            f"## Configuration `{config}`",
            "",
            "| Verdict | PROVEN | WEAK | UNPROVEN | REFUTED | INCONCLUSIVE |",
            "|---|---|---|---|---|---|",
            "| Cases | "
            + " | ".join(
                str(block["verdicts"][v])
                for v in ("PROVEN", "WEAK", "UNPROVEN", "REFUTED", "INCONCLUSIVE")
            )
            + " |",
            "",
            "| Kind | Flagged strict | Flagged lenient |",
            "|---|---|---|",
        ]
        for kind, k in block["by_kind"].items():
            out.append(
                f"| {kind} | {_frac(k['flagged_strict'], k['cases'])} | {_frac(k['flagged_lenient'], k['cases'])} |"
            )
        out += [
            "",
            "| Ablation | Gamed recall, strict | Gamed recall, lenient | Honest false alarms, lenient |",
            "|---|---|---|---|",
            f"| full pipeline | {_pct(block['recall_strict'])} | {_pct(block['recall_lenient'])} | {_pct(block['false_alarm_lenient'])} |",
        ]
        for drop in ABLATIONS:
            a = block["ablations"][drop]
            out.append(
                f"| {ABLATION_NAMES[drop]} | {_pct(a['recall_strict'])} | {_pct(a['recall_lenient'])} | {_pct(a['false_alarm_lenient'])} |"
            )
        out += [
            "",
            f"Median runtime {block['median_runtime_s']} s per run; median recorded LLM cost "
            f"${block['median_recorded_usd']:.4f} per run; verdict engine reproduced every run: "
            f"{'yes' if block['engine_consistent'] else 'NO'}; LLM errors: {block['llm_errors']}"
            + (
                f"; same verdict on repeat {_pct(block['determinism'])}, same evidence "
                f"{_pct(block['evidence_determinism'])}"
                if "determinism" in block
                else ""
            )
            + ".",
        ]
    if report.leakage is not None:
        leak = report.leakage
        out += [
            "",
            "## Equivalent-mutant leakage",
            "",
            f"{leak.get('labelled', 0)} of {leak.get('total', 0)} survivors labelled by a human; "
            + (
                f"leakage {_pct(leak.get('leakage'))} (target <= 10%)."
                if leak.get("labelled")
                else "not measured yet (no labels)."
            ),
        ]
    out += [
        "",
        "## Cases",
        "",
        "| Case | Label | Kind | " + " | ".join(summary) + " |",
        "|---|---|---|" + "---|" * len(summary),
    ]
    by_case: dict[str, dict[str, str]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for r in report.results:
        by_case.setdefault(r.case, {})[r.config] = f"{r.verdict} ({r.rule_id})"
        meta[r.case] = (r.label, r.kind)
    for case, verdicts in by_case.items():
        label, kind = meta[case]
        out.append(
            f"| {case} | {label} | {kind} | "
            + " | ".join(verdicts.get(c, "") for c in summary)
            + " |"
        )
    return "\n".join(out) + "\n"
