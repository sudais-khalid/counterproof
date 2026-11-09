"""The Counterproof demo: real runs, captured once, rendered as a video and an animated SVG.

    uv run python scripts/make_demo.py capture        # run the scenes, keep their output
    uv run python scripts/make_demo.py svg            # docs/assets/demo.svg (README)
    <python with pillow + imageio-ffmpeg> scripts/make_demo.py video   # docs/media/*.mp4

``capture`` runs every scene for real (fixture repositories, seed 00..00, no network) and
stores the exact terminal output, colours included, in docs/demo/capture.json. The renderers
only draw what was captured: the demo cannot show anything Counterproof did not print. The
narration is the voice-over of docs/demo-script.md, shown as captions.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CAPTURE = ROOT / "docs" / "demo" / "capture.json"
SVG_OUT = ROOT / "docs" / "assets" / "demo.svg"
VIDEO_OUT = ROOT / "docs" / "media" / "counterproof-demo.mp4"
SEED = "00" * 16
COLUMNS = 92

SCENES: list[dict[str, Any]] = [
    {
        "key": "task",
        "title": "An agent fixed a bug and says: all tests pass",
        "caption": "Coding agents stop when their tests pass. But when the same agent writes the "
        "fix and the test, green is a claim, not evidence. Counterproof checks the claim.",
        "command": "cat task.md",
        "kind": "task",
    },
    {
        "key": "hardcoded",
        "title": "1. Hard-coded fix",
        "caption": "This fix special-cases the exact inputs its own test uses. The integrity "
        "probe sees an input compared with a test literal: refuted, before any mutant runs.",
        "command": "counterproof verify",
        "variant": "hardcoded",
    },
    {
        "key": "partial",
        "title": "2. Partial fix",
        "caption": "This one looks honest and its test fails on the old code. But wrong versions "
        "of the changed line still pass every test: nothing tests a discount at or below 20%.",
        "command": "counterproof verify",
        "variant": "partial",
    },
    {
        "key": "coach",
        "title": "3. Coach mode, inside Claude Code",
        "caption": "In Claude Code the Stop hook tells the agent only what kind of test is "
        "missing and where. Never the faulty code, so it cannot write a test for our mutant.",
        "command": "counterproof verify --mode coach",
        "variant": "partial",
        "args": ["--mode", "coach"],
    },
    {
        "key": "claim",
        "title": "4. \u201cThe test suite passes\u201d",
        "caption": "The agent said the suite passes. Its session piped pytest through tail, "
        "which hid exit code 1, and Counterproof's own baseline fails. Refuted.",
        "command": "counterproof verify --transcript session.jsonl",
        "variant": "honest",
        "overlay": "c2/broken-cap",
        "transcript": "tests/fixtures/transcripts/c2/masked-green.jsonl",
    },
    {
        "key": "eval",
        "title": "5. Measured, not claimed",
        "caption": "On the seeded corpus, green tests flag none of the gamed changes. "
        "Counterproof refutes every hard-coded and weakened one. Every number regenerates.",
        "command": "counterproof eval eval/corpora/c1",
        "kind": "eval",
    },
]


# -- capture ------------------------------------------------------------------------------


def _verify(scene: dict[str, Any], work: Path) -> str:
    from tests.helpers import FIXTURE_REPOS, build_repo  # noqa: PLC0415

    overlay = FIXTURE_REPOS / "py_ledger" / scene["overlay"] if scene.get("overlay") else None
    repo = build_repo(
        work / scene["key"], task="T1-discount-cap", variant=scene["variant"], overlay=overlay
    )
    args = ["verify", "--seed", SEED, *scene.get("args", [])]
    if scene.get("transcript"):
        args += ["--transcript", str(ROOT / scene["transcript"])]
    env = {
        **os.environ,
        "FORCE_COLOR": "1",
        "COLUMNS": str(COLUMNS),
        "COUNTERPROOF_CACHE_DIR": str(work / "cache"),
        "COUNTERPROOF_PYTHON": sys.executable,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "counterproof", *args],
        cwd=repo.path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    text = proc.stdout
    # The run id and timing differ on every machine; they do not belong in a recording.
    local = ("Evidence: run", "Run directory:")  # differ per machine; never in a recording
    kept = [ln for ln in text.splitlines() if not ln.startswith(local)]
    return "\n".join(kept).rstrip() + "\n"


def _eval_table() -> str:
    results = sorted((ROOT / "eval" / "results").glob("*-c1.md"))
    text = results[-1].read_text(encoding="utf-8")
    block = text.split("## Headline", 1)[1].split("\n## ", 1)[0].strip()
    rows = [r for r in block.splitlines() if r.startswith("|") and not r.startswith("|---")]
    cells = [[c.strip() for c in r.strip("|").split("|")][:5] for r in rows]
    widths = [max(len(c[i]) for c in cells) for i in range(5)]
    widths[0] = min(widths[0], 34)
    out = []
    for i, row in enumerate(cells):
        line = "  ".join(c[: widths[j]].ljust(widths[j]) for j, c in enumerate(row))
        out.append(f"\x1b[1m{line}\x1b[0m" if i == 0 else line)
    return "\n".join(out) + "\n"


def capture() -> None:
    work = Path(tempfile.mkdtemp(prefix="cp-demo-"))
    out = []
    for scene in SCENES:
        if scene.get("kind") == "task":
            text = (
                ROOT / "tests/fixtures/repos/py_ledger/tasks/T1-discount-cap/task.md"
            ).read_text(encoding="utf-8")
        elif scene.get("kind") == "eval":
            text = _eval_table()
        else:
            text = _verify(scene, work)
        out.append({**scene, "output": text})
        print(scene["key"], len(text.splitlines()), "lines")
    CAPTURE.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# -- ANSI ---------------------------------------------------------------------------------

PALETTE = {
    30: "#0e1b2c", 31: "#ff6a5d", 32: "#4cc38a", 33: "#e2a643", 34: "#8ba9ff",
    35: "#c792ea", 36: "#6fd3e3", 37: "#e4e9f2", 90: "#6b778c", 91: "#ff8a80",
    92: "#7be0a8", 93: "#f2c36b", 94: "#a9bfff", 95: "#d6a8f0", 96: "#93e4ef", 97: "#ffffff",
}  # fmt: skip
FG = "#e4e9f2"
BG = "#0a1220"
_SGR = re.compile(r"\x1b\[([0-9;]*)m")


@dataclass
class Span:
    text: str
    color: str
    bold: bool


def _xterm(n: int) -> str:
    if n < 16:
        base = [30, 31, 32, 33, 34, 35, 36, 37, 90, 91, 92, 93, 94, 95, 96, 97]
        return PALETTE[base[n]]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return f"#{steps[n // 36]:02x}{steps[(n // 6) % 6]:02x}{steps[n % 6]:02x}"
    level = 8 + (n - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"


def parse_ansi(line: str) -> list[Span]:
    spans: list[Span] = []
    color, bold, pos = FG, False, 0
    for m in _SGR.finditer(line):
        if m.start() > pos:
            spans.append(Span(line[pos : m.start()], color, bold))
        codes = [int(c) for c in m.group(1).split(";") if c] or [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                color, bold = FG, False
            elif c == 1:
                bold = True
            elif c == 22:
                bold = False
            elif c == 39:
                color = FG
            elif c in PALETTE:
                color = PALETTE[c]
            elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                color, i = _xterm(codes[i + 2]), i + 2
            elif c == 38 and i + 4 < len(codes) and codes[i + 1] == 2:
                color = f"#{codes[i + 2]:02x}{codes[i + 3]:02x}{codes[i + 4]:02x}"
                i += 4
            i += 1
        pos = m.end()
    if pos < len(line):
        spans.append(Span(line[pos:], color, bold))
    return spans


_VERDICT_COLOR = {
    "PROVEN": "\x1b[1;32m",
    "WEAK": "\x1b[1;33m",
    "UNPROVEN": "\x1b[1;33m",
    "REFUTED": "\x1b[1;31m",
    "INCONCLUSIVE": "\x1b[1;90m",
}
_RESET = "\x1b[0m"
_HEADINGS = ("Why:", "Surviving faults", "Integrity findings:", "Claim audit findings:", "##")


def colorize(line: str) -> str:
    """Colour for the plain-text certificate; the words are exactly what Counterproof printed."""
    for verdict, code in _VERDICT_COLOR.items():
        line = re.sub(rf"(?<![\w-]){verdict}(?![\w-])", f"{code}{verdict}{_RESET}", line)
    line = re.sub(r"\[((?:R|FR)-[A-Z0-9-]+)\]", f"\x1b[94m[\\1]{_RESET}", line)
    line = re.sub(r"\b(PASS|OK)\b", f"\x1b[32m\\1{_RESET}", line)
    line = line.replace("Missing test:", f"\x1b[1;33mMissing test:{_RESET}", 1)
    if line.startswith(_HEADINGS):
        line = f"\x1b[1m{line}{_RESET}"
    return line


def styled_lines(text: str, width: int) -> list[str]:
    """Wrap plain lines to ``width`` (continuations indented), then colour each one."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            cut = cut if cut > width // 2 else width
            out.append(line[:cut])
            line = "      " + line[cut:].lstrip()
        out.append(line)
    return [colorize(ln) for ln in out]


def _scenes() -> list[dict[str, Any]]:
    return json.loads(CAPTURE.read_text(encoding="utf-8"))


# -- SVG ----------------------------------------------------------------------------------


def _tspan(span: Span) -> str:
    bold = ' class="b"' if span.bold else ""
    return f'<tspan fill="{span.color}"{bold}>{html.escape(span.text)}</tspan>'


def svg(keys: tuple[str, ...] = ("hardcoded", "partial", "claim")) -> None:
    """An animated terminal: each scene types its command, prints, holds, then gives way."""
    scenes = [s for s in _scenes() if s["key"] in keys]
    char_w, line_h, pad = 8.4, 18.0, 18
    width = int(COLUMNS * char_w + 2 * pad)
    for scene in scenes:
        scene["lines"] = styled_lines(scene["output"], COLUMNS)
    max_lines = max(len(s["lines"]) for s in scenes) + 3
    height = int(max_lines * line_h + 2 * pad + 34)
    per_scene = [4.0 + 0.04 * len(s["lines"]) + 5.0 for s in scenes]
    total = sum(per_scene)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="Counterproof verifying three '
        "AI-written changes: a hard-coded fix is REFUTED, a partial fix is WEAK, and a false "
        '&quot;tests pass&quot; claim is REFUTED">',
        "<style>text{font-family:'JetBrains Mono',ui-monospace,Consolas,monospace;"
        "font-size:13.5px;white-space:pre}.b{font-weight:700}"
        "@media (prefers-reduced-motion:reduce){.sc,.ln{animation:none!important}"
        ".sc{opacity:0}.sc:last-of-type{opacity:1}.ln{opacity:1}}</style>",
        f'<rect width="{width}" height="{height}" rx="10" fill="{BG}"/>',
        '<circle cx="20" cy="17" r="5.5" fill="#ff5f57"/><circle cx="38" cy="17" r="5.5" '
        'fill="#febc2e"/><circle cx="56" cy="17" r="5.5" fill="#28c840"/>',
    ]
    start = 0.0
    for scene, duration in zip(scenes, per_scene, strict=True):
        a, b = start / total * 100, (start + duration) / total * 100
        sid = f"s{scene['key']}"
        parts.append(
            f"<style>@keyframes {sid}{{0%,{a:.3f}%{{opacity:0}}{a + 0.01:.3f}%,"
            f"{b - 0.01:.3f}%{{opacity:1}}{b:.3f}%,100%{{opacity:0}}}}"
            f"#{sid}{{animation:{sid} {total:.1f}s infinite step-end}}</style>"
        )
        parts.append(f'<g id="{sid}" class="sc" opacity="0">')
        y = pad + 34
        title = html.escape(scene["title"])
        parts.append(f'<text x="{pad}" y="{y}" fill="#8ba9ff" class="b"># {title}</text>')
        y += line_h
        command = html.escape(scene["command"])
        parts.append(
            f'<text x="{pad}" y="{y}" fill="#4cc38a" class="b">$ <tspan fill="{FG}">'
            f"{command}</tspan></text>"
        )
        for i, line in enumerate(scene["lines"]):
            y += line_h
            delay = start + 1.2 + 0.04 * i
            lid = f"{sid}l{i}"
            parts.append(
                f"<style>@keyframes {lid}{{0%,{delay / total * 100:.3f}%{{opacity:0}}"
                f"{delay / total * 100 + 0.01:.3f}%,100%{{opacity:1}}}}"
                f"#{lid}{{animation:{lid} {total:.1f}s infinite step-end}}</style>"
            )
            tspans = "".join(_tspan(sp) for sp in parse_ansi(line))
            parts.append(f'<text id="{lid}" class="ln" x="{pad}" y="{y}">{tspans}</text>')
        parts.append("</g>")
        start += duration
    parts.append("</svg>")
    SVG_OUT.parent.mkdir(parents=True, exist_ok=True)
    SVG_OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"{SVG_OUT.relative_to(ROOT)}: {total:.0f}s loop, {SVG_OUT.stat().st_size // 1024} KB")


# -- video --------------------------------------------------------------------------------


class _Painter:
    """Draws one 1280x720 frame: title, typed command, output so far, caption bar."""

    width, height, line_h = 1280, 720, 18

    def __init__(self) -> None:
        from PIL import ImageFont  # noqa: PLC0415

        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"

        def font(names: tuple[str, ...], size: int) -> Any:
            for name in names:
                for base in (fonts, Path("/usr/share/fonts/truetype/dejavu")):
                    if (base / name).is_file():
                        return ImageFont.truetype(str(base / name), size)
            return ImageFont.load_default()

        self.mono = font(("consola.ttf", "DejaVuSansMono.ttf"), 14)
        self.mono_b = font(("consolab.ttf", "DejaVuSansMono-Bold.ttf"), 14)
        self.caption = font(("segoeui.ttf", "DejaVuSans.ttf"), 22)
        self.title = font(("georgiab.ttf", "DejaVuSerif-Bold.ttf"), 26)
        self.char_w = self.mono.getlength("M")

    def lines(self, scene: dict[str, Any]) -> list[str]:
        return styled_lines(scene["output"], int((self.width - 80) // self.char_w))

    def _caption_rows(self, text: str) -> list[str]:
        rows, row = [], ""
        for word in text.split():
            if self.caption.getlength(f"{row} {word}".strip()) > self.width - 120:
                rows.append(row)
                row = word
            else:
                row = f"{row} {word}".strip()
        return [*rows, row]

    def frame(self, scene: dict[str, Any], typed: str, shown: int) -> Any:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        img = Image.new("RGB", (self.width, self.height), BG)
        d = ImageDraw.Draw(img)
        d.text((40, 26), scene["title"], font=self.title, fill="#8ba9ff")
        y = 76
        d.text((40, y), "$ ", font=self.mono_b, fill="#4cc38a")
        d.text((40 + 2 * self.char_w, y), typed, font=self.mono, fill=FG)
        room = (self.height - 150 - y) // self.line_h
        for line in self.lines(scene)[:shown][:room]:
            y += self.line_h
            x = 40.0
            for sp in parse_ansi(line):
                d.text((x, y), sp.text, font=self.mono_b if sp.bold else self.mono, fill=sp.color)
                x += self.char_w * len(sp.text)
        d.rectangle((0, self.height - 120, self.width, self.height), fill="#101a2c")
        for i, text in enumerate(self._caption_rows(scene["caption"])[:3]):
            d.text((60, self.height - 104 + i * 30), text, font=self.caption, fill=FG)
        return img


def _timeline(painter: _Painter) -> list[tuple[Any, float]]:
    """``(frame, seconds)``: type the command, print the output, hold on the caption."""
    frames: list[tuple[Any, float]] = []
    for scene in _scenes():
        command = scene["command"]
        for i in range(len(command) + 1):
            frames.append((painter.frame(scene, command[:i], 0), 0.045))
        frames.append((painter.frame(scene, command, 0), 0.6))
        total = len(painter.lines(scene))
        step = max(1, total // 40)
        for shown in range(step, total + step, step):
            frames.append((painter.frame(scene, command, min(shown, total)), 0.05))
        hold = max(10.0, 0.6 * len(scene["caption"].split()))  # time to read the caption
        frames.append((painter.frame(scene, command, total), hold))
    return frames


def video(fps: int = 30) -> None:
    """1280x720 H.264: per scene a typed command, the output, and the narration as a caption."""
    import imageio_ffmpeg  # noqa: PLC0415

    painter = _Painter()
    VIDEO_OUT.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(VIDEO_OUT),
        (painter.width, painter.height),
        fps=fps,
        codec="libx264",
        pix_fmt_out="yuv420p",
        output_params=["-movflags", "+faststart", "-crf", "23"],
    )
    writer.send(None)
    seconds = 0.0
    for img, duration in _timeline(painter):
        repeats = max(1, round(duration * fps))
        data = img.tobytes()
        for _ in range(repeats):
            writer.send(data)
        seconds += repeats / fps
    writer.close()
    size = VIDEO_OUT.stat().st_size // 1024
    print(f"{VIDEO_OUT.relative_to(ROOT)}: {seconds:.0f}s, {size} KB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["capture", "svg", "video"])
    args = parser.parse_args()
    {"capture": capture, "svg": svg, "video": video}[args.stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
