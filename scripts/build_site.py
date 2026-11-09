"""Build the Counterproof website (GitHub Pages) into _site/.

Every number on the site is read from the committed evaluation results (eval/results/), and
the certificates in the hero are real Counterproof output (site/data/certificates/, written
by scripts/site_certificates.py), so the site cannot drift from what the tool measured.

    uv run python scripts/build_site.py [--out _site] [--base-url https://...]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2

from counterproof import __version__

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
RESULTS = ROOT / "eval" / "results"
BASE_URL = "https://sudais-khalid.github.io/counterproof"
REPO_URL = "https://github.com/sudais-khalid/counterproof"
PAGES = {
    "index.html": ("index.html.j2", "/"),
    "docs/index.html": ("docs.html.j2", "/docs/"),
    "integrations/index.html": ("integrations.html.j2", "/integrations/"),
    "results/index.html": ("results.html.j2", "/results/"),
    "faq/index.html": ("faq.html.j2", "/faq/"),
    "404.html": ("404.html.j2", "/404.html"),
}


@dataclass
class Certificate:
    key: str
    tab: str
    verdict: str
    rule: str
    summary: str
    probes: list[tuple[str, str]] = field(default_factory=list)
    why: list[str] = field(default_factory=list)


def parse_certificate(key: str, tab: str, text: str) -> Certificate:
    """The Markdown certificate (``verify --format md``) as card data."""
    head = re.search(r"certificate: (\w+)\s+\(rule ([\w-]+)\)", text)
    if head is None:
        raise SystemExit(f"{key}: not a Counterproof certificate")
    summary = next((ln for ln in text.splitlines() if ln.startswith("Change:")), "").removeprefix(
        "Change: "
    )
    probes = [
        (m.group(1).strip(), m.group(2).strip())
        for m in re.finditer(r"^\| (?!Probe|-)([^|]+)\| ([^|]+)\|$", text, flags=re.M)
    ]
    why_block = text.split("Why:", 1)[1].split("\n\n", 1)[0] if "Why:" in text else ""
    why = [
        re.sub(r"^\[[\w-]+\]\s*", "", ln.removeprefix("- ")).strip()
        for ln in why_block.splitlines()
        if ln.startswith("- ")
    ]
    return Certificate(key, tab, head.group(1), head.group(2), summary, probes, why[:3])


def certificates() -> list[Certificate]:
    index = json.loads((SITE / "data" / "certificates" / "index.json").read_text("utf-8"))
    return [
        parse_certificate(
            item["key"],
            item["tab"],
            (SITE / "data" / "certificates" / f"{item['key']}.md").read_text("utf-8"),
        )
        for item in index
    ]


def latest(corpus: str) -> dict[str, Any] | None:
    found = sorted(RESULTS.glob(f"*-{corpus}.json"))
    return json.loads(found[-1].read_text("utf-8")) if found else None


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{round(value * 100)}%"


def results_context() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for corpus in ("c1", "c2", "c3"):
        report = latest(corpus)
        if report is None:
            continue
        configs = []
        for name, block in report["summary"].items():
            configs.append(
                {
                    "name": name,
                    "gamed": block.get("gamed", 0),
                    "honest": block.get("honest", 0),
                    "recall_strict": pct(block.get("recall_strict")),
                    "recall_lenient": pct(block.get("recall_lenient")),
                    "false_strict": pct(block.get("false_alarm_strict")),
                    "false_lenient": pct(block.get("false_alarm_lenient")),
                    "runtime": block.get("median_runtime_s"),
                    "cost": block.get("median_recorded_usd"),
                    "baselines": {
                        b: {"recall": pct(v.get("recall")), "false": pct(v.get("false_alarm"))}
                        for b, v in block.get("baselines", {}).items()
                    },
                    "verdicts": block.get("verdicts", {}),
                    "inconclusive": block.get("inconclusive", 0),
                    "determinism": pct(block.get("determinism")),
                    "by_kind": block.get("by_kind", {}),
                }
            )
        out[corpus] = {
            "report": report,
            "configs": configs,
            "cases": len({r["case"] for r in report["results"]}),
            "date": report["created_at"][:10],
        }
    return out


def build(out: Path, base_url: str) -> None:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(SITE / "templates"),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():  # empty it, but keep the directory (a preview server may hold it)
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(SITE / "static", out / "static")
    context = {
        "version": __version__,
        "base_url": base_url.rstrip("/"),
        "repo_url": REPO_URL,
        "certificates": certificates(),
        "results": results_context(),
        "built": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    for target, (template, path) in PAGES.items():
        html = env.get_template(template).render(**context, path=path)
        dest = out / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
    urls = [p for _, p in PAGES.values() if p != "/404.html"]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(
            f"  <url><loc>{context['base_url']}{p}</loc>"
            f"<lastmod>{context['built']}</lastmod></url>\n"
            for p in urls
        )
        + "</urlset>\n",
        encoding="utf-8",
    )
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {context['base_url']}/sitemap.xml\n",
        encoding="utf-8",
    )
    (out / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "_site")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    build(args.out, args.base_url)
    print(f"built {len(PAGES)} pages into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
