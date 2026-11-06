"""The Markdown Proof Certificate (PRD section 14): the canonical human view of a run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from counterproof.models import Finding, ProbeResult
from counterproof.report.bundle import RunBundle

PROBE_ROWS = (
    ("Red-green", "red_green"),
    ("Integrity", "integrity"),
    ("Mutants", "mutants"),
    ("Blind oracle", "blind_oracle"),
    ("Claim audit", "claim_audit"),
)


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _short(nodeid: str) -> str:
    return nodeid.rsplit("::", 1)[-1]


def _code(text: str) -> str:
    return "`" + " ".join(text.split()).replace("`", "'") + "`"


def _red_green(probe: ProbeResult, bundle: RunBundle) -> str:
    signals = probe.signals
    cases = bundle.red_green_cases
    failing = [_short(c["nodeid"]) for c in cases if c.get("base_outcome") in {"fail", "error"}]
    passing = [_short(c["nodeid"]) for c in cases if c.get("base_outcome") in {"pass", "skip"}]
    if "SHORT_CIRCUIT" in signals:
        return "skipped (verdict already decided)"
    if "RG_NOT_APPLICABLE" in signals:
        return "not applicable (test-only change)"
    if "RG_STRONG" in signals:
        shown = ", ".join(failing[:3])
        return f"PASS: {len(failing)} of {len(cases)} test(s) fail on the old code ({shown})"
    if "RG_API_ONLY" in signals:
        return "WEAK: tests fail on the old code only because the new API is missing"
    if "RG_PASS_BOTH" in signals:
        return f"FAIL: {', '.join(passing[:3]) or 'the tests'} pass on the old code too"
    if "RG_NO_CANDIDATES" in signals:
        return "FAIL: no test exercises the change"
    return f"INCONCLUSIVE ({'/'.join(signals)})"


def _integrity(probe: ProbeResult) -> str:
    if "SHORT_CIRCUIT" in probe.signals:
        return "skipped"
    active = [f for f in probe.findings if not f.waived]
    waived = len(probe.findings) - len(active)
    high = sum(1 for f in active if f.severity == "high")
    medium = sum(1 for f in active if f.severity == "medium")
    suffix = f" ({waived} waived)" if waived else ""
    if high:
        return f"FAIL: {high} high, {medium} medium finding(s){suffix}"
    if medium:
        return f"WARN: {medium} medium finding(s){suffix}"
    return f"OK (0 findings){suffix}"


def _mutants(probe: ProbeResult) -> str:
    if "SHORT_CIRCUIT" in probe.signals:
        return "skipped (verdict already decided)"
    m = probe.metrics
    valid = int(m.get("valid", 0))
    if not valid:
        return "no valid mutants could be generated"
    text = f"{int(m.get('killed', 0))}/{valid} killed ({m.get('kill_ratio', 0.0):.2f})"
    survived = int(m.get("survived", 0))
    if survived:
        text += f". {survived} survivor(s)"
    equivalent = int(m.get("equivalent", 0))
    if equivalent:
        text += f"; {equivalent} judged equivalent and excluded"
    high = int(m.get("high_plausibility_survivors", 0))
    if high:
        text += f", {high} high-plausibility"
    return text


def _probe_cell(name: str, probes: Mapping[str, ProbeResult], bundle: RunBundle) -> str:
    probe = probes.get(name)
    if probe is None:
        if name == "blind_oracle":
            return "not run (arrives in M5)"
        if name == "claim_audit":
            return "not run (arrives in M5)"
        return "not run"
    if name == "red_green":
        return _red_green(probe, bundle)
    if name == "integrity":
        return _integrity(probe)
    if name == "mutants":
        return _mutants(probe)
    return f"{probe.status} {'/'.join(probe.signals)}"


def _findings(findings: Sequence[Finding]) -> list[str]:
    lines = []
    for f in findings:
        where = f"{f.file}:{f.line}" if f.file and f.line else f.file or "change"
        waiver = f" (waived: {f.waiver_reason})" if f.waived else ""
        proposed = [r for r in f.evidence_refs if r.startswith("proposed waiver")]
        note = f" ({proposed[0]})" if proposed else ""
        lines.append(f"- [{f.severity}] {f.rule_id} {where}: {f.message}{waiver}{note}")
    return lines


def _llm_line(bundle: RunBundle) -> str:
    """PRD 15.2: the certificate always states which provider saw code."""
    llm = bundle.manifest.llm
    if llm is None or llm.provider == "none":
        note = f" ({llm.note})" if llm is not None and llm.note else ""
        return f"LLM: not used{note}. No code was sent to any provider."
    text = (
        f"LLM: {llm.provider} ({', '.join(llm.models) or 'no calls'}): {llm.calls} call(s), "
        f"{llm.cached} from cache, {llm.tokens:,} tokens, ${llm.usd:.3f}. "
        f"Changed code was sent to {llm.provider}; {llm.redactions} secret(s) redacted."
    )
    if llm.skipped:
        text += f" Budget skipped: {'; '.join(llm.skipped[:3])}."
    if llm.errors:
        text += f" Failed: {'; '.join(llm.errors[:3])}."
    return text


def render_markdown(bundle: RunBundle) -> str:
    manifest = bundle.manifest
    verdict = manifest.verdict
    change = manifest.change
    probes: dict[str, ProbeResult] = {p.probe: p for p in manifest.probes}
    added = sum(1 for h in change.hunks for line in h.lines if line.startswith("+"))
    removed = sum(1 for h in change.hunks for line in h.lines if line.startswith("-"))
    usd = sum(p.cost.usd for p in manifest.probes)
    profile = bundle.config.get("run", {}).get("profile", "?")

    out = [
        f"## Counterproof certificate: {verdict.verdict}  (rule {verdict.rule_id})",
        f"Change: {len(change.files)} files, +{added} -{removed} | intent: {change.intent} | "
        f"profile: {profile} | {_duration(manifest.duration_s)} | ${usd:.2f}",
        "",
        "| Probe        | Result |",
        "|--------------|--------|",
    ]
    out += [f"| {label:<12} | {_probe_cell(name, probes, bundle)} |" for label, name in PROBE_ROWS]

    if verdict.reasons:
        out += ["", "Why:"]
        out += [f"- {reason}" for reason in verdict.reasons]

    survivors = [m for m in bundle.mutants if m.status == "survived"]
    if survivors:
        out += ["", "Surviving faults (would ship undetected):"]
        for i, m in enumerate(survivors, start=1):
            plaus = f", plausibility {m.plausibility}" if m.plausibility else ""
            out.append(
                f"{i}. {m.file}:{m.start_line}  {_code(m.original)} -> {_code(m.replacement)}"
                f"   ({m.operator_or_class}{plaus}, mutant {m.id})"
            )
            if m.hint:
                out.append(f"   Missing test: {m.hint}")

    integrity = probes.get("integrity")
    if integrity is not None and integrity.findings:
        out += ["", "Integrity findings:", *_findings(integrity.findings)]

    if bundle.baseline is not None and (bundle.baseline.flaky or bundle.baseline.known_flaky):
        flaky = bundle.baseline.flaky + bundle.baseline.known_flaky
        out += ["", f"Flaky tests excluded: {', '.join(flaky)}"]

    if verdict.confidence_note:
        out += ["", f"Scope: {verdict.confidence_note}"]
    out += ["", _llm_line(bundle)]
    out += [
        "",
        f"Evidence: run {manifest.run_id}  manifest sha256 {bundle.manifest_sha256[:12]}  "
        f"seed commitment {manifest.seed_commitment[:12]}",
    ]
    return "\n".join(out) + "\n"
