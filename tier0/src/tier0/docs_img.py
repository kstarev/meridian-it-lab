"""The README's two animated flow pictures, generated so they're reproducible:

    python -m tier0.docs_img --out docs/img

It writes a light and a dark version of each:

- happy-path.svg / happy-path-dark.svg: a request moves from Slack to "ticket closed", and each
  step lights up as it passes.
- attack-path.svg / attack-path-dark.svg: a request for a colleague's login reaches the
  own-account check, turns red and stops, and is handed to a person.

The motion is CSS inside the SVG, so it plays where GitHub shows the image. It's one calm 6 s
loop, with slow fades and nothing that flashes. With prefers-reduced-motion it doesn't move
and shows its final frame. The output is the same on every run.
"""

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

LOOP_S = 6
BOX_W, BOX_H, GAP, MARGIN = 150, 56, 40, 12
BOX_Y = 12
TRACK_Y = BOX_Y + BOX_H + 22
HEIGHT = TRACK_Y + 30
FADE = 3  # % of the loop a step takes to light up: 0.18 s
END = 92  # % of the loop where everything has stopped; then it fades back to the start

FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'

THEMES: Mapping[str, Mapping[str, str]] = {
    "light": {
        "text": "#1f2328",
        "muted": "#59636e",
        "fill": "#f6f8fa",
        "stroke": "#d0d7de",
        "lit_fill": "#ddf4ff",
        "lit_stroke": "#0969da",
        "ok_fill": "#dafbe1",
        "ok_stroke": "#1a7f37",
        "bad_fill": "#ffebe9",
        "bad_stroke": "#cf222e",
        "track": "#d0d7de",
    },
    "dark": {
        "text": "#e6edf3",
        "muted": "#9198a1",
        "fill": "#161b22",
        "stroke": "#30363d",
        "lit_fill": "#0c2d4f",
        "lit_stroke": "#4493f8",
        "ok_fill": "#0f2d1b",
        "ok_stroke": "#3fb950",
        "bad_fill": "#3a1014",
        "bad_stroke": "#f85149",
        "track": "#30363d",
    },
}


@dataclass(frozen=True)
class Step:
    lines: tuple[str, ...]
    lit_at: float  # % of the loop when the dot reaches this step; -1: never lit
    look: str = "lit"  # "lit", "ok" (the happy end) or "bad" (the check that stops it)


@dataclass(frozen=True)
class Flow:
    name: str
    title: str
    desc: str
    steps: tuple[Step, ...]
    dot_path: tuple[tuple[float, int], ...]  # (% of the loop, step index the dot is at)
    dot_turns_red_at: float | None = None
    caption: tuple[int, str, float] | None = None  # (under step, text, % it appears)
    dashed_after: int | None = None  # the track after this step is dashed: a hand-off


HAPPY = Flow(
    name="happy-path",
    title="A request that Tier0 answers",
    desc=(
        "Animation of a request moving through Tier0: you ask in Slack, it checks it's really "
        "you, it looks up only your own account, it replies, and the ticket is closed."
    ),
    steps=(
        Step(("You ask", "in Slack"), 3),
        Step(("Is it", "really you?"), 22),
        Step(("Your account", "only"), 41),
        Step(("It replies",), 60),
        Step(("Ticket", "closed"), 79, look="ok"),
    ),
    dot_path=((3, 0), (8, 0), (22, 1), (27, 1), (41, 2), (46, 2), (60, 3), (65, 3), (79, 4)),
)

ATTACK = Flow(
    name="attack-path",
    title="A request that Tier0 stops",
    desc=(
        "Animation of a request for a colleague's login: Tier0 checks it's really you, then "
        "the check for your own account only turns red and stops the request, and it is "
        "handed to a person."
    ),
    steps=(
        Step(("“What's my", "colleague's login?”"), 3),
        Step(("Is it", "really you?"), 24),
        Step(("Your account", "only?"), 46, look="bad"),
        Step(("Handed to", "a person"), 64),
    ),
    dot_path=((3, 0), (8, 0), (24, 1), (29, 1), (46, 2)),
    dot_turns_red_at=46,
    caption=(2, "Not your account", 52),
    dashed_after=2,
)

FLOWS = (HAPPY, ATTACK)


def _x(i: int) -> float:
    return MARGIN + i * (BOX_W + GAP)


def _cx(i: int) -> float:
    return _x(i) + BOX_W / 2


def _pct(p: float) -> str:
    return f"{p:g}%"


def _keyframes(name: str, frames: Sequence[tuple[float, str]]) -> str:
    body = " ".join(f"{_pct(p)} {{ {style} }}" for p, style in frames)
    return f"@keyframes {name} {{ {body} }}"


def _step_css(flow: Flow, i: int, t: Mapping[str, str]) -> tuple[str, str, str]:
    """The step's rule, its keyframes and its final frame (for reduced motion)."""
    step = flow.steps[i]
    base = f"fill: {t['fill']}; stroke: {t['stroke']}"
    lit = f"fill: {t[step.look + '_fill']}; stroke: {t[step.look + '_stroke']}"
    sel = f".s{i}"
    rule = f"{sel} {{ {base}; animation: k{i} {LOOP_S}s ease-in-out infinite; }}"
    on = step.lit_at
    frames = [(0, base), (max(on - FADE, 0.5), base), (on, lit), (END, lit), (98, base)]
    return rule, _keyframes(f"k{i}", frames), f"{sel} {{ {lit}; }}"


def _dot_css(flow: Flow, t: Mapping[str, str]) -> tuple[str, str, str]:
    first = flow.dot_path[0][1]
    last_p, last_i = flow.dot_path[-1]
    x0 = _cx(first)

    def at(i: int, opacity: int, color: str) -> str:
        return f"transform: translateX({_cx(i) - x0:g}px); opacity: {opacity}; fill: {color}"

    blue = t["lit_stroke"]
    red = t["bad_stroke"]
    end_color = red if flow.dot_turns_red_at is not None else blue
    frames: list[tuple[float, str]] = [(0, at(first, 0, blue))]
    for p, i in flow.dot_path:
        color = blue
        if flow.dot_turns_red_at is not None and p > flow.dot_turns_red_at:
            color = red
        frames.append((p, at(i, 1, color)))
    if flow.dot_turns_red_at is not None:
        frames.append((flow.dot_turns_red_at + FADE, at(last_i, 1, red)))
    frames += [(END, at(last_i, 1, end_color)), (97, at(last_i, 0, end_color))]
    frames.append((100, at(first, 0, blue)))
    rule = f"#dot {{ animation: kd {LOOP_S}s ease-in-out infinite; fill: {blue}; }}"
    final = f"#dot {{ {at(last_i, 1, end_color)}; }}"
    del last_p
    return rule, _keyframes("kd", frames), final


def _caption_css(flow: Flow) -> tuple[str, str, str]:
    if flow.caption is None:
        return "", "", ""
    _, _, appears = flow.caption
    frames = [(0, "opacity: 0"), (appears - FADE, "opacity: 0"), (appears, "opacity: 1")]
    frames += [(END, "opacity: 1"), (98, "opacity: 0")]
    rule = f"#caption {{ opacity: 0; animation: kc {LOOP_S}s ease-in-out infinite; }}"
    return rule, _keyframes("kc", frames), "#caption { opacity: 1; }"


def render(flow: Flow, theme: str) -> str:
    t = THEMES[theme]
    n = len(flow.steps)
    width = 2 * MARGIN + n * BOX_W + (n - 1) * GAP
    rules, frames, finals = [], [], []
    for part in (
        *(_step_css(flow, i, t) for i in range(n)),
        _dot_css(flow, t),
        _caption_css(flow),
    ):
        rule, keyframes, final = part
        if rule:
            rules.append(rule)
            frames.append(keyframes)
            finals.append(final)
    css = "\n".join(
        [
            f"text {{ font-family: {FONT}; font-size: 13px; font-weight: 500; "
            f"fill: {t['text']}; text-anchor: middle; }}",
            f"#caption {{ fill: {t['bad_stroke']}; font-size: 12px; }}",
            "rect { stroke-width: 1.5; }",
            *rules,
            *frames,
            "@media (prefers-reduced-motion: reduce) {",
            "  .s0, .s1, .s2, .s3, .s4, #dot, #caption { animation: none; }",
            *(f"  {f}" for f in finals),
            "}",
        ]
    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {HEIGHT}" '
        f'width="{width:g}" height="{HEIGHT}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(flow.title)}</title>',
        f'<desc id="desc">{escape(flow.desc)}</desc>',
        f"<style>\n{css}\n</style>",
    ]
    # The track under the steps, with a marker under each one.
    for i in range(n - 1):
        dash = ' stroke-dasharray="5 5"' if flow.dashed_after == i else ""
        out.append(
            f'<line x1="{_cx(i):g}" y1="{TRACK_Y}" x2="{_cx(i + 1):g}" y2="{TRACK_Y}" '
            f'stroke="{t["track"]}" stroke-width="2"{dash}/>'
        )
    for i in range(n):
        out.append(f'<circle cx="{_cx(i):g}" cy="{TRACK_Y}" r="4" fill="{t["track"]}"/>')
    for i, step in enumerate(flow.steps):
        out.append(
            f'<rect class="s{i}" x="{_x(i):g}" y="{BOX_Y}" width="{BOX_W}" height="{BOX_H}" '
            f'rx="10"/>'
        )
        mid = BOX_Y + BOX_H / 2
        first = mid - 8 * (len(step.lines) - 1) + 4.5
        for j, line in enumerate(step.lines):
            out.append(f'<text x="{_cx(i):g}" y="{first + 16 * j:g}">{escape(line)}</text>')
    if flow.caption is not None:
        under, text, _ = flow.caption
        out.append(
            f'<text id="caption" x="{_cx(under):g}" y="{TRACK_Y + 22}">{escape(text)}</text>'
        )
    out.append(f'<circle id="dot" cx="{_cx(flow.dot_path[0][1]):g}" cy="{TRACK_Y}" r="7"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def file_name(flow: Flow, theme: str) -> str:
    return f"{flow.name}.svg" if theme == "light" else f"{flow.name}-{theme}.svg"


def files() -> dict[str, str]:
    return {file_name(f, theme): render(f, theme) for f in FLOWS for theme in THEMES}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.docs_img", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/img"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files().items():
        (args.out / name).write_text(text)
        print(f"wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
