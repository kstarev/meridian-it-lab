"""The project site's social preview (site/og.png), generated so it can be redrawn:

    uv run --locked --with pillow --with fonttools --with brotli python -m tier0.site_og

or `make og`. It draws a 1200 x 630 PNG in the site's colours and fonts (site/fonts, converted
from WOFF2 on the fly): the Tier0 mark, the hero line, a five-step flow and three numbers.

The three numbers are the site's key numbers, and like them they are scoped to the test they
come from: the first sealed comparison (held-out v3). `numbers()` rebuilds them from the
published comparison, and tier0/tests/test_site_og.py checks them against the site's tiles.
Pillow and fontTools are not project dependencies, so they are imported only when drawing.
"""

import argparse
import io
import json
from functools import cache
from pathlib import Path
from typing import Any

from tier0.evals import compare_many as cm

ROOT = Path(__file__).resolve().parents[3]
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"
FONTS = ROOT / "site/fonts"
OUT = ROOT / "site/og.png"

W, H = 1200, 630
SCALE = 2  # drawn at twice the size, then scaled down, so shapes are smooth

# The site's colours (site/index.html).
BG = "#0B1220"
CARD = "#0E1729"
CARD_EDGE = "#1F2C47"
ACCENT = "#2563EB"
BLUE = "#60A5FA"
BLUE_LABEL = "#8FB8F6"
TEXT = "#F3F6FB"
MUTED = "#B6C0D3"
FAINT = "#8D99B0"
NODE = "#13284A"
NODE_EDGE = "#2A4A7F"
TRACK = "#2A3A5C"
AMBER = "#F59E0B"
AMBER_NODE = "#2A2112"

STEPS = (
    ("chat", "You ask in Slack"),
    ("shield", "Checks it's really you"),
    ("person", "Your account only"),
    ("handoff", "Risky? A person decides"),
    ("check", "Ticket closed"),
)
URL = "github.com/kstarev/meridian-it-lab"
HERO = ("IT help that\u2019s awake", "when IT isn\u2019t.")  # the site's h1, typographic quotes
SUBTITLE = (
    "A Slack assistant that answers simple IT questions, and",
    "hands anything risky to a person.",
)


@cache
def numbers() -> dict[str, int]:
    """The first sealed comparison's numbers, from the published held-out v3 comparison."""
    comparison = json.loads(COMPARISON.read_text())
    reports = [COMPARISON.parent / Path(p).name for p in comparison["reports"]]
    raw = [json.loads(path.read_text()) for path in reports]
    views = [cm.view(r) for r in raw]
    (k,) = {v["k"] for v in views}
    (tickets,) = {len(v["tickets"]) for v in views}
    return {
        "unsafe": sum(r["unsafe_runs"] for r in raw),
        "setups": len(views),
        "best_percent": round(100 * max(v["pass_k"] for v in views)),
        "k": k,
        "tickets": tickets,
    }


def caption() -> str:
    """The line over the three numbers, which scopes them to their test."""
    return f"First sealed comparison · {numbers()['tickets']} requests"


def stats() -> tuple[tuple[str, str], ...]:
    """(big number, label) for each of the three numbers, left to right."""
    n = numbers()
    return (
        (str(n["unsafe"]), "unsafe actions"),
        (str(n["setups"]), "AI models tested"),
        (f"{n['best_percent']}%", f"right on all {n['k']} tries"),
    )


def _font(name: str, size: int) -> Any:
    """A site font at `size` px (before scaling), converted from WOFF2 in memory."""
    from fontTools.ttLib import TTFont
    from PIL import ImageFont

    font = TTFont(str(FONTS / f"{name}.woff2"))
    font.flavor = None
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, size * SCALE)


def _s(*values: float) -> tuple[int, ...]:
    return tuple(round(v * SCALE) for v in values)


def _icon(draw: Any, kind: str, cx: float, cy: float, colour: str) -> None:
    """Simple line icons, after the site's SVG icons (24-unit boxes, centred on cx, cy)."""
    w = 2 * SCALE

    def p(x: float, y: float) -> tuple[int, int]:
        return round((cx + (x - 12) * 0.85) * SCALE), round((cy + (y - 12) * 0.85) * SCALE)

    if kind == "chat":
        draw.line([p(4, 5), p(20, 5), p(20, 16), p(9, 16), p(4, 20), p(4, 5)], fill=colour, width=w)
    elif kind == "shield":
        draw.line(
            [
                p(12, 3),
                p(19, 6),
                p(19, 12),
                p(16, 18),
                p(12, 21),
                p(8, 18),
                p(5, 12),
                p(5, 6),
                p(12, 3),
            ],
            fill=colour,
            width=w,
            joint="curve",
        )
        draw.line([p(9, 12), p(11, 14), p(15, 10)], fill=colour, width=w, joint="curve")
    elif kind in ("person", "handoff"):
        x0 = 9 if kind == "handoff" else 12
        draw.ellipse([*p(x0 - 4, 4), *p(x0 + 4, 12)], outline=colour, width=w)
        draw.arc([*p(x0 - 8, 15), *p(x0 + 8, 31)], 190, 350, fill=colour, width=w)
        if kind == "handoff":
            draw.line([p(16, 11), p(21, 11)], fill=colour, width=w)
            draw.line([p(18.5, 8.5), p(21, 11), p(18.5, 13.5)], fill=colour, width=w)
    elif kind == "check":
        draw.line([p(5, 12), p(10, 17), p(20, 7)], fill=colour, width=w, joint="curve")


def draw() -> Any:
    """The preview image, 1200 x 630."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", _s(W, H), BG)
    d = ImageDraw.Draw(img)
    grotesk = _font("space-grotesk-latin-700-normal", 60)
    grotesk_logo = _font("space-grotesk-latin-700-normal", 22)
    grotesk_name = _font("space-grotesk-latin-700-normal", 28)
    grotesk_stat = _font("space-grotesk-latin-700-normal", 40)
    inter = _font("inter-latin-400-normal", 21)
    inter_label = _font("inter-latin-400-normal", 16)
    inter_step = _font("inter-latin-600-normal", 17)
    mono = _font("jetbrains-mono-latin-500-normal", 13)
    mono_url = _font("jetbrains-mono-latin-400-normal", 14)

    d.rectangle([*_s(0, 0), *_s(7, H)], fill=ACCENT)

    # The mark and the name.
    d.rounded_rectangle(
        [*_s(68, 60), *_s(118, 110)], radius=12 * SCALE, fill=NODE, outline=NODE_EDGE, width=SCALE
    )
    t_w = d.textlength("T", font=grotesk_logo)
    zero_w = d.textlength("0", font=grotesk_logo)
    x = (93 * SCALE) - (t_w + zero_w) / 2
    d.text((x, 85 * SCALE), "T", font=grotesk_logo, fill=TEXT, anchor="lm")
    d.text((x + t_w, 85 * SCALE), "0", font=grotesk_logo, fill=BLUE, anchor="lm")
    d.text(_s(134, 85), "Tier0", font=grotesk_name, fill=TEXT, anchor="lm")

    # The hero line and what it is.
    for i, line in enumerate(HERO):
        d.text(_s(66, 140 + 68 * i), line, font=grotesk, fill=TEXT)
    for i, line in enumerate(SUBTITLE):
        d.text(_s(68, 296 + 32 * i), line, font=inter, fill=MUTED)

    # The three numbers, under the line that scopes them.
    d.text(_s(68, 440), caption().upper(), font=mono, fill=BLUE_LABEL)
    columns = zip(stats(), (68, 238, 418), (BLUE, TEXT, TEXT), strict=True)
    for (value, label), left, colour in columns:
        d.text(_s(left, 470), value, font=grotesk_stat, fill=colour)
        d.text(_s(left, 522), label, font=inter_label, fill=MUTED)

    # The flow card.
    d.rounded_rectangle(
        [*_s(790, 88), *_s(1132, 478)], radius=18 * SCALE, fill=CARD, outline=CARD_EDGE, width=SCALE
    )
    cx, top, step = 846, 146, 68.5
    d.line([*_s(cx, top), *_s(cx, top + step * (len(STEPS) - 1))], fill=TRACK, width=2 * SCALE)
    for i, (kind, label) in enumerate(STEPS):
        cy = top + step * i
        edge, fill = (AMBER, AMBER_NODE) if kind == "handoff" else (BLUE, NODE)
        box = [*_s(cx - 27, cy - 27), *_s(cx + 27, cy + 27)]
        d.ellipse(box, fill=fill, outline=edge, width=2 * SCALE)
        _icon(d, kind, cx, cy, AMBER if kind == "handoff" else "#DBEAFE")
        d.text(_s(892, cy), label, font=inter_step, fill=TEXT, anchor="lm")
    d.text(_s(1132, 508), URL, font=mono_url, fill=FAINT, anchor="rm")

    return img.resize((W, H), Image.Resampling.LANCZOS)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    draw().save(args.out, optimize=True)
    print(f"wrote {args.out} ({W} x {H})")


if __name__ == "__main__":
    main()
