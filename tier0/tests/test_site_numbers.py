"""The project site's numbers (site/index.html), checked against the published reports.

The site is hand-written static HTML, so nothing generates its numbers. Each test here rebuilds
one of them from the reports (held-out v3, and held-out v2 for the "before" score), or from the
repo itself, and requires the site to say it word for word. If a report or the page changes, a
test fails. The one exception is the test count, which only has to stay true: the site may not
claim more tests than the suite has, but adding a test doesn't make the page wrong.

Not checked here: og.png (an image) and the design settings under "Under the hood" (the
5-minute token, the 3-day auto-close), which are settings, not results.
"""

import json
import re
import subprocess
import sys
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from tier0.evals import compare_many as cm

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site/index.html"
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"
M2_COMPARISON = ROOT / "docs/evals/2026-11-09-held-out-v2-comparison.json"

# How the site names each setup in its results chart.
LABELS = {
    "sonnet": "Claude Sonnet",
    "haiku": "Claude Haiku",
    "gpt-oss:20b": "gpt-oss 20B",
    "qwen3.6:27b": "Qwen 3.6 27B",
    "llama3.1:8b": "Llama 3.1 8B",
    "llama3.2": "Llama 3.2",
    "mixed": "Mixed (two Llamas)",
}
HOSTED, OPEN = "Hosted", "Open model, benchmark only"
AXIS_FROM, AXIS_TO = 90, 100  # the chart's x axis, in % of requests right on all 3 tries


class _Text(HTMLParser):
    """The page's visible text, one chunk per text node, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("style", "script", "svg", "head"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "svg", "head"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self._skip:
            self.chunks.append(text)


@cache
def _html() -> str:
    return SITE.read_text()


@cache
def _chunks() -> tuple[str, ...]:
    parser = _Text()
    parser.feed(_html())
    return tuple(parser.chunks)


def _says(text: str) -> None:
    assert text in " ".join(_chunks()), f"site/index.html no longer says, word for word: {text!r}"


def _says_in_order(*chunks: str) -> None:
    """Consecutive text nodes, e.g. a big number and the label under it."""
    n = len(chunks)
    found = any(_chunks()[i : i + n] == chunks for i in range(len(_chunks())))
    assert found, f"site/index.html no longer shows, in order: {chunks!r}"


@cache
def _comparison() -> dict[str, Any]:
    return json.loads(COMPARISON.read_text())


@cache
def _raw() -> tuple[dict[str, Any], ...]:
    return tuple(
        json.loads((COMPARISON.parent / Path(p).name).read_text()) for p in _comparison()["reports"]
    )


@cache
def _views() -> tuple[dict[str, Any], ...]:
    return tuple(cm.view(r) for r in _raw())


def _k() -> int:
    (k,) = {v["k"] for v in _views()}
    return k


def _n_tickets() -> int:
    (n,) = {len(v["tickets"]) for v in _views()}
    return n


def _percent(score: float) -> int:
    return round(100 * score)


# The key numbers under the hero.


def test_key_numbers() -> None:
    for r in _raw():
        assert r["unsafe_runs"] == r["policy_violation_runs"] == r["data_exposed_runs"] == 0
    _says_in_order("0", "unsafe actions, across every AI model tested")
    scores = [round(v["pass_k"], 9) for v in _views()]
    assert scores.count(max(scores)) > 1  # "best setups", plural
    _says_in_order(
        f"{_percent(max(scores))}%", f"of requests right on all {_k()} tries (best setups)"
    )
    _says_in_order(str(_n_tickets()), "test requests, written blind and sealed until the final run")


def test_test_count_is_not_overstated() -> None:
    (claimed,) = [
        int(c.replace(",", ""))
        for i, c in enumerate(_chunks())
        if _chunks()[i + 1 : i + 2] == ("automated tests, run on every change",)
    ]
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = re.search(r"^(\d+) tests? collected", collected, re.MULTILINE)
    assert found, collected[-500:]
    assert claimed <= int(found.group(1)), f"the site claims {claimed} tests, the suite has fewer"


def test_decision_records() -> None:
    adrs = re.findall(r"^## ADR-(\d+)", (ROOT / "docs/decisions.md").read_text(), re.MULTILINE)
    assert [int(n) for n in adrs] == list(range(1, len(adrs) + 1))
    _says(f"{len(adrs)} decision records")


def test_meridian_facts_come_from_the_brief() -> None:
    brief = (ROOT / "docs/brief.md").read_text()
    assert "| People | 230, fully remote |" in brief
    assert "| Footprint | 14 countries," in brief
    assert "Meridian's 3-person IT team" in brief
    _says("Meridian has 230 people in 14 countries and an IT team of three")


# What the testing showed.


def test_results_heading_and_method() -> None:
    _says(f"{len(_views())} AI models. None broke a safety rule.")
    _says(
        f"Someone who never saw the code wrote {_n_tickets()} requests, and they stayed locked "
        f"away until the final run. Each request ran {_k()} times on every setup, and only "
        f"counted if all {_k()} tries were right."
    )


def test_everyday_before_and_after() -> None:
    """Before: milestone 2's best everyday pass^3 on its own sealed set (held-out v2).
    After: the range across every setup on the final sealed set (held-out v3)."""
    m2 = json.loads(M2_COMPARISON.read_text())
    before = max(json.loads((ROOT / p).read_text())["everyday_pass_k"] for p in m2["reports"])
    after = [v["everyday_pass_k"] for v in _views()]
    _says_in_order(
        f"Everyday requests right on all {_k()} tries",
        f"{_percent(before)}%",
        f"{_percent(min(after))}\N{EN DASH}{_percent(max(after))}%",
    )


def test_claude_cost() -> None:
    claude = {cm.name(r): cm._api_cost(r, per=1000) for r in _raw() if not cm._is_local(r)}
    assert set(claude) == {"haiku", "sonnet"}
    _says_in_order(
        "Cost per 1,000 requests with Claude",
        claude["haiku"],
        "Haiku",
        claude["sonnet"],
        "Sonnet",
    )


def test_chart_axis() -> None:
    for v in _views():
        assert AXIS_FROM <= _percent(v["pass_k"]) <= AXIS_TO, cm.name(v)
    labels = re.findall(r'<span style="[^"]*left: (\d+)%[^"]*">(\d+)%</span>', _html())
    assert labels == [
        (str((p - AXIS_FROM) * 100 // (AXIS_TO - AXIS_FROM)), str(p))
        for p in range(AXIS_FROM, AXIS_TO + 1, 2)
    ]


def test_chart_rows() -> None:
    """One row per setup: name, type, a dot at its pass^3, unsafe actions and cost."""
    assert {cm.name(r) for r in _raw()} == set(LABELS)
    html = _html()
    starts = [(html.index(f">{label}</span>"), name) for name, label in LABELS.items()]
    starts.sort()
    order = []
    for (start, name), (end, _) in zip(starts, [*starts[1:], (len(html), "")], strict=True):
        row = html[start:end]
        (r,) = [r for r in _raw() if cm.name(r) == name]
        (v,) = [v for v in _views() if cm.name(v) == name]
        local = cm._is_local(r)
        cost = cm.NO_API_COST if local else cm._api_cost(r, per=1000)
        _says_in_order(LABELS[name], OPEN if local else HOSTED, str(r["unsafe_runs"]), cost)
        (dot,) = re.findall(r'<div style="([^"]*border-radius: 50%[^"]*)"', row)
        left = (_percent(v["pass_k"]) - AXIS_FROM) * 100 // (AXIS_TO - AXIS_FROM)
        assert f"left: {left}%" in dot, (name, dot)
        assert ("border:" in dot) == local, f"{name}: hollow dots are open models"
        order.append(v["pass_k"])
    assert order == sorted(order, reverse=True)  # best first


def test_chart_caption() -> None:
    assert _comparison()["verdict"]["winner"] is None
    _says(
        f"Each dot is the share of the {_n_tickets()} requests a setup got right on all "
        f"{_k()} tries."
    )
    _says(f"With {_n_tickets()} requests, no setup was clearly better than the others.")
