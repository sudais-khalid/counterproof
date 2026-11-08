"""Evaluation harness (PRD section 17): ``counterproof eval CORPUS_DIR``.

Every case of a corpus is materialised as a git repository and verified under each
configuration (for example rule-only and recorded-LLM), with a fixed seed and with
short-circuiting off, so that every probe runs: verdicts are unchanged (the rule order is the
same) but baselines and ablations can be computed for refuted changes too.

Per case the harness records the verdict, the baselines and ablations, and runtime and
recorded cost:

* B0 "green tests only": flags a change when the head tests fail.
* B1 "claim audit only": flags any CLAIM_* contradiction (needs a transcript).
* B2 "diff-scoped classic mutation": flags any rule mutant on the changed lines that the
  tests do not kill, with no equivalence filtering (what mutmut or Stryker restricted to
  the diff would report). Counterproof's own RuleForge stands in for those tools.
* Ablations: the pure verdict engine re-decides with one probe removed (or with rule mutants
  only); nothing is re-run. The engine must reproduce the run's own verdict first.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from counterproof import __version__
from counterproof.config import Config
from counterproof.errors import UsageError
from counterproof.eval.corpus import Case, Configuration, Corpus, load_corpus, materialise
from counterproof.llm.cache import CASSETTE_DIR_ENV, CASSETTE_MODE_ENV
from counterproof.log import get_logger
from counterproof.models import ProbeResult, Verdict
from counterproof.orchestrator import VerifyRequest, run_verify
from counterproof.probes.mutants.probe import KILLED, _score
from counterproof.report.bundle import RunBundle, load_bundle
from counterproof.verdict.rules import DecisionInput, decide, early_verdict

log = get_logger("eval")

STRICT: Final = frozenset({"UNPROVEN", "REFUTED"})
LENIENT: Final = STRICT | {"WEAK"}
ABLATIONS: Final = (
    "claim_audit",
    "integrity",
    "red_green",
    "mutants",
    "llm_mutants",
    "blind_oracle",
)
BASELINES: Final = ("B0", "B1", "B2")


@dataclass
class CaseResult:
    case: str
    config: str
    label: str
    kind: str
    verdict: str
    rule_id: str
    flagged_strict: bool
    flagged_lenient: bool
    baselines: dict[str, bool | None]
    ablations: dict[str, str]
    duration_s: float
    recorded_usd: float
    run_id: str
    engine_consistent: bool
    llm_errors: int = 0
    signature: str = ""
    """Verdict, probe signals and mutant outcomes: equal across repeats if deterministic."""
    repeat_verdicts: list[str] = field(default_factory=list)
    """``verdict (rule)`` of every repeat after the first (``--repeat``)."""
    repeats_identical: bool | None = None
    """Evidence-level: same signals, metrics and mutant outcomes in every repeat."""


@dataclass
class EvalReport:
    corpus: str
    description: str
    created_at: str
    tool_version: str
    seed: str
    results: list[CaseResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    leakage: dict[str, Any] | None = None
    baselines: list[str] = field(default_factory=lambda: list(BASELINES))


# -- per case ------------------------------------------------------------------------------


def _config_of(bundle: RunBundle) -> Config:
    return Config.model_validate(bundle.config)


def _decision(bundle: RunBundle, probes: Sequence[ProbeResult]) -> DecisionInput:
    manifest = bundle.manifest
    build_error = manifest.verdict.reasons[0] if manifest.verdict.rule_id == "R-INC-BUILD" else None
    llm = manifest.llm
    return DecisionInput(
        manifest.change.intent,
        build_error=build_error,
        baseline=bundle.baseline,
        probes=list(probes),
        llm_enabled=llm is not None and llm.provider != "none",
        llm_skipped=len(llm.skipped) if llm else 0,
    )


def replay_verdict(bundle: RunBundle, probes: Sequence[ProbeResult] | None = None) -> Verdict:
    """The verdict the pipeline reaches from these results, on the same path: runs that
    stopped after rules 1-5 (flaky or red head, no probe beyond P5) use ``early_verdict``."""
    config = _config_of(bundle)
    manifest = bundle.manifest
    inp = _decision(bundle, manifest.probes if probes is None else probes)
    stopped_early = manifest.verdict.rule_id != "R-INC-BUILD" and not any(
        p.probe == "integrity" for p in manifest.probes
    )
    if stopped_early:
        early = early_verdict(inp, config)
        if early is not None:
            return early
    return decide(inp, config)


def ablate(bundle: RunBundle, drop: str) -> Verdict:
    """The verdict without one probe (``llm_mutants``: with rule mutants only)."""
    config = _config_of(bundle)
    probes = list(bundle.manifest.probes)
    if drop == "llm_mutants":
        rule = [m for m in bundle.mutants if m.source == "rule"]
        probes = [_score(rule, config, {}, 0.0) if p.probe == "mutants" else p for p in probes]
    else:
        probes = [p for p in probes if p.probe != drop]
    return replay_verdict(bundle, probes)


def baselines(bundle: RunBundle) -> dict[str, bool | None]:
    """B0, B1, B2 for one run; ``None`` where a baseline cannot run (no transcript)."""
    baseline = bundle.baseline
    claims = next((p for p in bundle.manifest.probes if p.probe == "claim_audit"), None)
    b1: bool | None = None
    if claims is not None and "CA_NO_TRANSCRIPT" not in claims.signals:
        b1 = any(s.startswith("CLAIM_") for s in claims.signals)
    rule = [
        m for m in bundle.mutants if m.source == "rule" and m.status not in {"invalid", "error"}
    ]
    return {
        "B0": bool(baseline and baseline.failing),
        "B1": b1,
        "B2": any(m.status not in KILLED for m in rule) if rule else False,
    }


def _outcome(status: str) -> str:
    # FR-MU-10: a timeout kills a mutant just like a failing test. Which of the two happens
    # depends on machine load (a mutant that allocates a huge array), not on the tests.
    return "killed" if status in KILLED else status


def _signature(bundle: RunBundle) -> str:
    manifest = bundle.manifest
    probes = [
        (p.probe, sorted(p.signals), sorted((k, v) for k, v in p.metrics.items() if k != "timeout"))
        for p in manifest.probes
    ]
    mutants = [(m.id, _outcome(m.status), m.replacement) for m in bundle.mutants]
    return json.dumps([manifest.verdict.verdict, manifest.verdict.rule_id, probes, mutants])


def run_case(
    corpus: Corpus, case: Case, configuration: Configuration, workdir: Path, cache: Path
) -> CaseResult:
    slug = case.id.replace("/", "__").replace("#", "_")
    built = materialise(corpus, case, workdir / configuration.name / slug)
    overrides: dict[str, Any] = {
        "run": {"profile": configuration.profile, "short_circuit": False},
        "llm": {"provider": configuration.provider},
    }
    request = VerifyRequest(
        cwd=built.path,
        base=built.base,
        head=built.head,
        # The task comes from the commit message, as in real use (FR-CR-04); recorded LLM
        # answers were made the same way, so their prompts match byte for byte.
        transcript=case.transcript,
        seed_hex=corpus.seed,
        overrides=overrides,
    )
    outcome = run_verify(request, cache=cache)
    bundle = load_bundle(outcome.run_dir)
    manifest = bundle.manifest
    verdict = manifest.verdict
    consistent = replay_verdict(bundle) == verdict
    llm = manifest.llm
    return CaseResult(
        case=case.id,
        config=configuration.name,
        label=case.label,
        kind=case.kind,
        verdict=verdict.verdict,
        rule_id=verdict.rule_id,
        flagged_strict=verdict.verdict in STRICT,
        flagged_lenient=verdict.verdict in LENIENT,
        baselines=baselines(bundle),
        ablations={d: ablate(bundle, d).verdict for d in ABLATIONS},
        duration_s=manifest.duration_s,
        recorded_usd=llm.recorded_usd if llm else 0.0,
        run_id=manifest.run_id,
        engine_consistent=consistent,
        llm_errors=len(llm.errors) if llm else 0,
        signature=_signature(bundle),
    )


# -- the whole corpus ------------------------------------------------------------------------


@contextmanager
def _cassettes(configuration: Configuration, cassettes: Path | None) -> Iterator[None]:
    """Recorded answers for this repo, or none; restores the environment afterwards."""
    saved = {k: os.environ.get(k) for k in (CASSETTE_DIR_ENV, CASSETTE_MODE_ENV)}
    try:
        if configuration.provider != "none" and configuration.replay:
            if cassettes is None:
                raise UsageError(
                    f"configuration {configuration.name!r} replays, but the repo has no cassettes"
                )
            os.environ[CASSETTE_DIR_ENV] = str(cassettes)
            os.environ[CASSETTE_MODE_ENV] = "replay"
        else:
            os.environ.pop(CASSETTE_DIR_ENV, None)
            os.environ.pop(CASSETTE_MODE_ENV, None)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_corpus(
    directory: Path,
    *,
    jobs: int = 4,
    allow_remote: bool = False,
    configurations: Sequence[str] | None = None,
    repeat: int = 1,
    workdir: Path | None = None,
) -> EvalReport:
    corpus = load_corpus(directory)
    if any(c.remote for c in corpus.cases) and not allow_remote:
        raise UsageError(
            "this corpus has git cases that clone and run foreign code; pass --allow-remote "
            "(preferably on an isolated machine or CI runner)"
        )
    chosen = [c for c in corpus.configurations if not configurations or c.name in configurations]
    if not chosen:
        raise UsageError(f"no configuration named {', '.join(configurations or [])}")
    report = EvalReport(
        corpus=corpus.name,
        description=corpus.description,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        tool_version=__version__,
        seed=corpus.seed,
    )
    base = workdir or Path(tempfile.mkdtemp(prefix="cp-eval-"))
    for configuration in chosen:
        groups: dict[str | None, list[Case]] = {}
        for case in corpus.cases:
            groups.setdefault(case.repo, []).append(case)
        for repo_name, cases in groups.items():
            cassettes = corpus.repos[repo_name].cassettes if repo_name in corpus.repos else None
            with _cassettes(configuration, cassettes), ThreadPoolExecutor(max_workers=jobs) as pool:
                for rep in range(repeat):
                    root = base / f"rep{rep}"
                    results = list(
                        pool.map(
                            lambda c, r=root, cfg=configuration: run_case(
                                corpus, c, cfg, r, r / "cache"
                            ),
                            cases,
                        )
                    )
                    for result in results:
                        log.info(
                            "%s [%s]: %s (%s)",
                            result.case,
                            configuration.name,
                            result.verdict,
                            result.rule_id,
                        )
                    if rep == 0:
                        report.results += results
                    else:
                        firsts = {(r.case, r.config): r for r in report.results}
                        for again in results:
                            first = firsts[(again.case, again.config)]
                            first.repeat_verdicts.append(f"{again.verdict} ({again.rule_id})")
                            same = first.signature == again.signature
                            first.repeats_identical = (
                                same
                                if first.repeats_identical is None
                                else (first.repeats_identical and same)
                            )
    report.summary = summarise(report.results)
    return report


# -- metrics -------------------------------------------------------------------------------


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def summarise(results: Sequence[CaseResult]) -> dict[str, Any]:
    """Per configuration: PRD 3.3 / 17.2 metrics, baselines, ablations, per-kind recall."""
    summary: dict[str, Any] = {}
    for config in dict.fromkeys(r.config for r in results):
        rows = [r for r in results if r.config == config]
        gamed = [r for r in rows if r.label == "gamed"]
        honest = [r for r in rows if r.label == "honest"]
        block: dict[str, Any] = {
            "cases": len(rows),
            "gamed": len(gamed),
            "honest": len(honest),
            "inconclusive": sum(1 for r in rows if r.verdict == "INCONCLUSIVE"),
            "recall_strict": _rate(sum(r.flagged_strict for r in gamed), len(gamed)),
            "recall_lenient": _rate(sum(r.flagged_lenient for r in gamed), len(gamed)),
            "false_alarm_strict": _rate(sum(r.flagged_strict for r in honest), len(honest)),
            "false_alarm_lenient": _rate(sum(r.flagged_lenient for r in honest), len(honest)),
            "median_runtime_s": round(statistics.median(r.duration_s for r in rows), 1)
            if rows
            else None,
            "median_recorded_usd": round(statistics.median(r.recorded_usd for r in rows), 4)
            if rows
            else None,
            "engine_consistent": all(r.engine_consistent for r in rows),
            "llm_errors": sum(r.llm_errors for r in rows),
        }
        block["verdicts"] = {
            v: sum(1 for r in rows if r.verdict == v)
            for v in ("PROVEN", "WEAK", "UNPROVEN", "REFUTED", "INCONCLUSIVE")
        }
        block["by_kind"] = {
            kind: {
                "cases": len(group),
                "flagged_strict": sum(r.flagged_strict for r in group),
                "flagged_lenient": sum(r.flagged_lenient for r in group),
            }
            for kind in sorted({r.kind for r in rows})
            for group in [[r for r in rows if r.kind == kind]]
        }
        block["baselines"] = {}
        for name in BASELINES:
            applicable = [r for r in rows if r.baselines.get(name) is not None]
            g = [r for r in applicable if r.label == "gamed"]
            h = [r for r in applicable if r.label == "honest"]
            block["baselines"][name] = {
                "applicable": len(applicable),
                "recall": _rate(sum(bool(r.baselines[name]) for r in g), len(gamed)),
                "false_alarm": _rate(sum(bool(r.baselines[name]) for r in h), len(honest)),
            }
        block["ablations"] = {}
        for drop in ABLATIONS:
            block["ablations"][drop] = {
                "recall_strict": _rate(sum(r.ablations[drop] in STRICT for r in gamed), len(gamed)),
                "recall_lenient": _rate(
                    sum(r.ablations[drop] in LENIENT for r in gamed), len(gamed)
                ),
                "false_alarm_lenient": _rate(
                    sum(r.ablations[drop] in LENIENT for r in honest), len(honest)
                ),
            }
        repeated = [r for r in rows if r.repeat_verdicts]
        if repeated:
            # PRD 3.3: "same inputs and seed produce the same verdict"
            block["determinism"] = _rate(
                sum(
                    all(v == f"{r.verdict} ({r.rule_id})" for v in r.repeat_verdicts)
                    for r in repeated
                ),
                len(repeated),
            )
            block["evidence_determinism"] = _rate(
                sum(bool(r.repeats_identical) for r in repeated), len(repeated)
            )
        summary[config] = block
    return summary


def load_report(path: Path) -> EvalReport:
    """A report written by :func:`write_report` (``<name>.json``)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    results = [CaseResult(**r) for r in data.pop("results", [])]
    return EvalReport(**data, results=results)


def merge_reports(reports: Sequence[EvalReport], *, corpus: str, description: str) -> EvalReport:
    """One report from several partial runs of the same corpus (C3 runs one pull request per
    CI job): results are concatenated in case order and the summary is recomputed."""
    if not reports:
        raise ValueError("no reports to merge")
    results = sorted((r for rep in reports for r in rep.results), key=lambda r: (r.case, r.config))
    first = reports[0]
    return EvalReport(
        corpus=corpus,
        description=description,
        created_at=max(rep.created_at for rep in reports),
        tool_version=first.tool_version,
        seed=first.seed,
        results=results,
        summary=summarise(results),
        baselines=first.baselines,
    )


def to_json(report: EvalReport) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=False) + "\n"


def write_report(report: EvalReport, out: Path) -> tuple[Path, Path]:
    """``<out>.json`` and ``<out>.md`` (``out`` without suffix, or a .json path)."""
    from counterproof.eval.table import render_table  # noqa: PLC0415

    json_path = out if out.suffix == ".json" else out.with_suffix(".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(to_json(report), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_table(report), encoding="utf-8")
    return json_path, md_path
