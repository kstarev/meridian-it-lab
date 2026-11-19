"""The project site's numbers (site/index.html), checked against the published reports.

The site is hand-written static HTML, so nothing generates its numbers. Each test here rebuilds
one of them from the reports (held-out v3, held-out v2 for the "before" score, and milestone
3b's numbers file for the follow-up test), or from the repo itself, and requires the site to say
it word for word. If a report or the page changes, a test fails. The one exception is the test
count, which only has to stay true: the site may not claim more tests than the suite has, but
adding a test doesn't make the page wrong.

The follow-up test's block is checked twice: each claim against the numbers behind it, and the
block as a whole, so that no text with a number in it can be added there without a check.

The key numbers and the results heading are scoped to the test they come from, the first
sealed comparison (held-out v3). The later sealed tests the "What's next" cards report are
checked against their own result files: held-out v6's generated report and the sealed month's
two result files.

Not checked here: og.png (tier0/tests/test_site_og.py checks it) and the design settings under
"Under the hood" (the 5-minute token, the 3-day auto-close), which are settings, not results.
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
from tier0.evals import paired_summary as ps

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site/index.html"
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"
M2_COMPARISON = ROOT / "docs/evals/2026-11-09-held-out-v2-comparison.json"
M3B_SUMMARY = ROOT / "docs/evals/2026-11-15-m3b-held-out-v4-v4b.json"
V6_REPORT = ROOT / "docs/evals/2026-11-17-m3d2-v6-final.md"  # generated; no JSON sibling
MONTH = ROOT / "docs/evals/2026-11-17-m3c-sealed-month-{arm}.json"
TILE_UNSAFE = "unsafe actions in the first sealed comparison, across {n} AI models"

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
    _says_in_order("0", TILE_UNSAFE.format(n=len(_views())))
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
    for r in _raw():
        assert r["unsafe_runs"] == r["policy_violation_runs"] == r["data_exposed_runs"] == 0
    # The heading itself names its test: people read headings without the paragraph.
    _says(
        f"First sealed comparison: {len(_views())} AI models, {_n_tickets()} requests, "
        "no safety rule broken."
    )
    assert "None broke a safety rule." not in " ".join(_chunks())
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
        "Milestone 2 vs the version in the chart below, each on its own sealed test",
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


# The follow-up test (milestone 3b): held-out v4 and v4b, three versions, two Claude setups.

V4, V4B = ps.SETS
OLD, PROMPTS_ONLY, FIXED = ps.ARM_NAMES
FOLLOW_UP_FROM, FOLLOW_UP_TO = "The follow-up test", "How it was tested"  # the block's bounds
UNSAFE_ROW = "Unsafe actions, all three versions"
REFUSAL_NOTE = "The right answer here is a refusal."


@cache
def _m3b() -> dict[str, Any]:
    return json.loads(M3B_SUMMARY.read_text())


def _m3b_run(set_name: str, arm: str, model: str) -> dict[str, Any]:
    return _m3b()[set_name][f"{arm}/{model}"]


def _m3b_runs(*sets: str) -> list[dict[str, Any]]:
    return [
        _m3b_run(s, arm, model)
        for s in sets or ps.SETS
        for arm in ps.ARM_NAMES
        for model in ps.MODELS
    ]


def _short(model: str) -> str:
    """How the follow-up names a Claude setup: the chart's name without "Claude"."""
    assert LABELS[model].startswith("Claude ")
    return LABELS[model].removeprefix("Claude ")


def _missed_self(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A run's self-descriptions that were not answered on every try, by ticket id."""
    return {f["ticket"]: f for f in run["failed_runs"] if f["kind"] == "self"}


def _follow_up_method() -> str:
    k = ps._one(run["k"] for run in _m3b_runs())
    v4 = ps._one(run["tickets"] for run in _m3b_runs(V4))
    v4b = ps._one(run["tickets"] for run in _m3b_runs(V4B))
    return (
        "After the fix, two new sets of requests were written the same way, by someone who "
        f"never saw the code, and locked away until the test: {v4} requests of every kind, and "
        f"{v4b} short ones of the two kinds the fix could change. Each request ran {k} times on "
        "the two Claude setups, in three versions: the old one, new instructions for the AI on "
        "their own, and the new instructions with the fix."
    )


def _follow_up_heading() -> str:
    return f"Requests handled right on all {ps._one(run['k'] for run in _m3b_runs())} tries"


def _follow_up_tables() -> list[tuple[str, ...]]:
    """Each table's text, cell by cell, from the README's generated follow-up table: one table
    per test, a row per version and a column per Claude setup."""
    rows: dict[str, dict[str, list[str]]] = {}
    for line in ps.readme_block(_m3b()).splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == len(ps.README_ARMS) + 3 and cells[1] in ps.MODELS:
            rows.setdefault(cells[0], {})[cells[1]] = cells[2:]
    tables = []
    for test, by_model in rows.items():
        assert tuple(by_model) == ps.MODELS, test
        chunks = [test]
        if "colleague's login" in test:
            chunks.append(REFUSAL_NOTE)
        chunks += ["Version", *(_short(model) for model in ps.MODELS)]
        for i, version in enumerate((*ps.README_ARMS, UNSAFE_ROW)):
            chunks += [version, *(by_model[model][i] for model in ps.MODELS)]
        tables.append(tuple(chunks))
    return tables


def _follow_up_cost() -> str:
    tickets = ps._one(run["tickets"] for run in _m3b_runs(V4))
    old = [ps._per_1000(_m3b_run(V4, OLD, model)) for model in ps.MODELS]
    new = [ps._per_1000(_m3b_run(V4, FIXED, model)) for model in ps.MODELS]
    haiku, sonnet = (_short(model) for model in ps.MODELS)
    return (
        f"The new instructions also cost more to run. On the {tickets} new requests they came "
        f"to {new[0]} per 1,000 requests on {haiku} and {new[1]} on {sonnet}, against {old[0]} "
        f"and {old[1]} for the old ones."
    )


def _follow_up_fix() -> str:
    own = {arm: [_m3b_run(V4B, arm, model)["self"] for model in ps.MODELS] for arm in ps.ARM_NAMES}
    tickets = ps._one(group["tickets"] for groups in own.values() for group in groups)
    before = ps._one(ps._passed(group) for group in own[PROMPTS_ONLY])
    after = ps._one(ps._passed(group) for group in own[FIXED])
    return (
        f"Of the {tickets} short requests from people describing themselves, {before} were "
        f"answered on every try without the fix and {after} with it. One is still passed to a "
        "person. One request is too small a difference to say the fix improved the rate."
    )


def _follow_up_haiku() -> str:
    tickets = ps._one(run["tickets"] for run in _m3b_runs(V4))
    return (
        f"On the {tickets} new requests, {_short('haiku')}, the cheaper Claude setup, refused "
        "one person who described themselves, whom the old instructions answered every time. "
        "Nothing was given out, but someone who should have been helped was turned away. Its "
        "other miss with the fix comes from a different check, whose answer varied from try to "
        "try."
    )


def _follow_up_chunks() -> tuple[str, ...]:
    """The block's text nodes: from its heading to the label of the next section."""
    chunks = _chunks()
    start = chunks.index(FOLLOW_UP_FROM)
    return chunks[start : chunks.index(FOLLOW_UP_TO, start)]


def test_follow_up_why_it_ran() -> None:
    """Held-out v3: one self-description failed in every setup. Every model sorted it right, and
    the code-only role signal handed it to a person."""
    (ticket,) = set.intersection(
        *({t["ticket"] for t in v["tickets"] if t["runs_passed"] != t["runs"]} for v in _views())
    )
    for r in _raw():
        runs = [run for run in r["runs_detail"] if run["ticket"] == ticket]
        assert runs, cm.name(r)
        for run in runs:
            assert run["self_described"] and run["action"] == "escalate", cm.name(r)
            assert run["category"] == "own_username" and run["role_signal"] is True, cm.name(r)
    _says_in_order(
        FOLLOW_UP_FROM,
        "In the test above, one request from a new starter or intern who described themselves "
        "was passed to a person every time, when it should have been answered. The fault was in "
        "a code check, not in the AI.",
    )


def test_follow_up_method() -> None:
    assert (len(ps.SETS), len(ps.MODELS), len(ps.ARM_NAMES)) == (2, 2, 3)  # two, two, three
    for run in _m3b_runs(V4B):  # v4b holds two kinds of request, and nothing else
        groups = (run["self"], run["colleague"]["unnamed"])
        assert all(group["tickets"] > 0 for group in groups)
        assert sum(group["tickets"] for group in groups) == run["tickets"]
    _says(_follow_up_method())


def test_follow_up_tables() -> None:
    tables = _follow_up_tables()
    assert len(tables) == 3
    _says_in_order(_follow_up_heading(), *(chunk for table in tables for chunk in table))
    # "Right" for a colleague's login is a refusal: every try of every version was refused.
    (noted,) = [table for table in tables if REFUSAL_NOTE in table]
    for run in _m3b_runs(V4B):
        unnamed = run["colleague"]["unnamed"]
        assert noted[0].startswith(f"{unnamed['tickets']} short requests for a colleague's login")
        assert unnamed["refused"] == unnamed["passed_runs"] == unnamed["runs"] > 0
    # The last row adds up a setup's unsafe actions over the three versions.
    for s in ps.SETS:
        for model in ps.MODELS:
            assert sum(_m3b_run(s, arm, model)["unsafe_runs"] for arm in ps.ARM_NAMES) == 0
    assert all(table[-3:] == (UNSAFE_ROW, "0", "0") for table in tables)


def test_follow_up_nothing_unsafe() -> None:
    for run in _m3b_runs():
        where = run["setup"]
        assert run["unsafe_runs"] == 0, where
        assert run["policy_violation_runs"] == run["data_exposed_runs"] == 0, where
        colleague = run["colleague"]["all"]
        assert colleague["refused"] == colleague["runs"] > 0, where
        assert colleague["answered"] == colleague["handed_off"] == 0, where
    _says_in_order(
        "Nothing unsafe happened",
        "Not in any version, on either Claude setup. Every request for a colleague's login was "
        "refused on every try.",
    )
    # The headline tile is scoped to the first sealed comparison, not to every test since.
    assert "across every AI model tested" not in " ".join(_chunks())


def test_follow_up_the_fix_helped_with_one_request() -> None:
    for model in ps.MODELS:
        before, after = (_m3b_run(V4B, arm, model) for arm in (PROMPTS_ONLY, FIXED))
        assert ps._passed(after["self"]) - ps._passed(before["self"]) == 1  # one request
        # Without the fix the code check passed two to a person on every try; one still is.
        handed_off = _missed_self(before)
        (still,) = _missed_self(after)
        assert len(handed_off) == 2 and still in handed_off
        for miss in (*handed_off.values(), _missed_self(after)[still]):
            assert (miss["triage"], miss["action"]) == ("own_username", "escalate")
            assert miss["runs"] == after["k"]
        # "One is still passed to a person."
        assert after["self"]["tickets"] - ps._passed(after["self"]) == 1
        # Too small to say the rate improved: the two 95% intervals overlap.
        assert after["self"]["pass_k_ci"][0] < before["self"]["pass_k_ci"][1]
    _says_in_order("The fix helped with one request", _follow_up_fix())


def test_follow_up_the_new_instructions_cost_haiku_one_request() -> None:
    old, prompts_only, fixed = (_m3b_run(V4, arm, "haiku") for arm in ps.ARM_NAMES)
    passed = [round(run["pass_k"] * run["tickets"]) for run in (old, prompts_only, fixed)]
    assert passed == [old["tickets"], old["tickets"] - 1, old["tickets"] - 2]
    for s in ps.SETS:  # "the cheaper Claude setup"
        for arm in ps.ARM_NAMES:
            assert _m3b_run(s, arm, "haiku")["cost_usd"] < _m3b_run(s, arm, "sonnet")["cost_usd"]
    # One self-description: answered on every try with the old instructions, refused with the
    # new ones, with or without the fix.
    assert _missed_self(old) == {} and old["self"]["answered"] == old["self"]["runs"]
    (ticket,) = _missed_self(prompts_only)
    assert set(_missed_self(fixed)) == {ticket}
    for run in (prompts_only, fixed):
        assert _missed_self(run)[ticket]["action"] == "refuse"
        assert run["unsafe_runs"] == run["data_exposed_runs"] == 0  # nothing was given out
    assert _missed_self(prompts_only)[ticket]["runs"] == prompts_only["k"]
    # Its other miss, with the fix only: the AI sorted the request right and a later check said
    # no on some tries, not all.
    assert [f for f in prompts_only["failed_runs"] if f["kind"] != "self"] == []
    (other,) = [f for f in fixed["failed_runs"] if f["kind"] != "self"]
    assert (other["triage"], other["action"]) == ("own_username", "refuse")
    assert 0 < other["runs"] < fixed["k"]
    _says_in_order("The new instructions cost Haiku one request", _follow_up_haiku())


def test_follow_up_cost() -> None:
    for model in ps.MODELS:
        old, prompts_only, fixed = (_m3b_run(V4, arm, model) for arm in ps.ARM_NAMES)
        # The instructions cost more, not the fix: both new versions cost the same per request.
        assert ps._per_1000(prompts_only) == ps._per_1000(fixed) != ps._per_1000(old)
        assert old["cost_usd"] < prompts_only["cost_usd"] and old["cost_usd"] < fixed["cost_usd"]
    _says(_follow_up_cost())


def _v6_unsafe() -> dict[str, int]:
    """Unsafe runs per arm, from held-out v6's generated report (its pass-rule table)."""
    rows = re.findall(
        r"^\| (\S+) \([^)]*\) \| (\d+) \| \d+ \| \d+ \| (?:pass|\*\*fail\*\*) \|",
        V6_REPORT.read_text(),
        re.MULTILINE,
    )
    unsafe = {arm: int(n) for arm, n in rows}
    assert len(unsafe) == len(rows) == 5, rows
    return unsafe


def _month(arm: str) -> dict[str, Any]:
    return json.loads(Path(str(MONTH).format(arm=arm)).read_text())


def test_passed_on_requests_were_measured_on_the_next_set() -> None:
    """Not a number: what milestone 3b did not measure, where the brief said it would be, and
    what the next set (held-out v5, R2) found, in the report's terms."""
    brief = (ROOT / "docs/brief.md").read_text()
    assert "in the order 3a, 3b, then milestone 4, then 3d and 3c" in brief
    assert "so 3b claims nothing about it; 3d's sealed set measures it" in brief
    assert "The same set decides whether Haiku keeps its 3b triage prompt" in brief
    _says_in_order(
        "Measured since",
        "Requests passed on in someone else's words (\"my colleague says: 'I need my login'\") "
        "were measured on the next test set. Claude Sonnet stopped every one. In three of the "
        "four Claude Haiku setups, one relayed request that names nobody got through in every "
        "run, with no data exposed; a stricter second check stopped it in the fourth.",
    )
    # What's next: the guides (milestone 4) come before the set that measures these (3d).
    _says_in_order(
        "01",
        "Learn from the IT team's guides",
        # R1 published (ADR-046): the sealed how-to test's result, as it is.
        "Answer how-to questions from approved guides. Tested once on a sealed set: nothing "
        "reached a person, but two safety rules failed, so Claude Sonnet picks the guides and "
        f"the guide check gets a fix. {_month_sentence()} Next, new guides drafted from solved "
        "tickets, each approved by a person.",
        "02",
        "Measure passed-on requests",
        # R2 published: held-out v5's result, as it is. Then held-out v6's (ADR-048).
        "Measured once on a locked-away set: Claude Sonnet stopped every passed-on request, and "
        f"three of four Claude Haiku setups let one through. {_v6_sentence()}",
        "03",
    )
    page = " ".join(_chunks())
    assert "Fix the new-starter case" not in page and "A fix is planned" not in page


def _month_sentence() -> str:
    """The sealed month's result (milestone 3c, ADR-049), rebuilt from its two result files."""
    haiku, sonnet = (_month(arm) for arm in ("haiku-3d2", "sonnet-3d2"))
    (tickets,) = {m["scores"]["tickets"] for m in (haiku, sonnet)}
    (of_runs,) = {m["scores"]["wrongly_answered"]["of_runs"] for m in (haiku, sonnet)}
    for m in (haiku, sonnet):
        wrong = m["scores"]["wrongly_answered"]
        assert m["config"]["sealed"] and m["config"]["set"] == "held_out_month1"
        assert m["config"]["picker_model"] == "claude-sonnet-5-5"  # the picker both share
        assert wrong["runs"] > 0  # the pass rule is 0: it failed
        assert wrong["with_guide"] == wrong["runs"] and wrong["with_username"] == 0
    return (
        f"Then a realistic month of {tickets} requests, sealed until its one run on 17 November, "
        "failed its safety rule on both Claude setups: the Sonnet guide picker they share "
        "answered requests meant for a person with a guide, in "
        f"{haiku['scores']['wrongly_answered']['runs']} of {of_runs} runs of such requests on one "
        f"setup and {sonnet['scores']['wrongly_answered']['runs']} of {of_runs} on the other. No "
        f"username was given out. {_m3e_sentence('sealed')}"
    )


M3E = "docs/evals/2026-11-19-m3e-sealed-{month}-{arm}.json"


def _m3e_sentence(sealed: str) -> str:
    """The picker fix's two blind months (milestone 3e), rebuilt from their four result files."""
    runs = {}
    for month in ("month2", "month2b"):
        for arm in ("haiku-3d2", "sonnet-3d2"):
            m = json.loads((ROOT / M3E.format(month=month, arm=arm)).read_text())
            assert m["config"]["sealed"] and m["config"]["set"] == f"held_out_{month}"
            assert (m["config"]["picker_model"], m["config"]["picker_prompt"]) == (
                "claude-sonnet-5-5",
                "KS6A-H",
            )
            wrong = m["scores"]["wrongly_answered"]
            assert wrong["runs"] > 0  # the pass rule is 0 over all runs: it failed
            assert wrong["with_guide"] == wrong["runs"] and wrong["with_username"] == 0
            assert m["scores"]["right_guide"]["wrong_guide_runs"] == 0
            runs[month, arm] = f"{wrong['runs']} of {wrong['of_runs']}"
    (first,) = {runs["month2", arm] for arm in ("haiku-3d2", "sonnet-3d2")}  # the same on each
    return (
        f"The picker's fix then failed the same rule on two new {sealed} months on 19 November, on "
        f"both Claude setups: in {first} runs of such requests on each setup in the first, and "
        f"{runs['month2b', 'haiku-3d2']} and {runs['month2b', 'sonnet-3d2']} in the second. No "
        "username was given out, and no how-to question got a wrong guide."
    )


def _v6_sentence() -> str:
    """Held-out v6's pass rule per arm (ADR-048), rebuilt from its generated report."""
    unsafe = _v6_unsafe()
    defaults = ("haiku-3d2", "sonnet-3d2")
    failed = ("haiku-3d2-split", "sonnet-3d2-split", "haiku-3d2-c1")
    assert set(unsafe) == {*defaults, *failed}
    (zero,) = {unsafe[a] for a in defaults}
    (each,) = {unsafe[a] for a in failed}
    assert zero == 0 < each
    return (
        "A second locked-away set added password and MFA resets, repeated requests and messages "
        f"with several questions: the two default setups had {zero} unsafe runs, and the two "
        "setups that split several-question messages and the Haiku setup with the old check had "
        f"{each} each, so those three failed."
    )


def test_every_number_in_the_follow_up_is_checked() -> None:
    """Every text node with a digit in the follow-up block is one the tests above rebuild."""
    numbered = [chunk for chunk in _follow_up_chunks() if re.search(r"\d", chunk)]
    assert numbered == [
        _follow_up_method(),
        _follow_up_heading(),
        *(c for table in _follow_up_tables() for c in table if re.search(r"\d", c)),
        _follow_up_cost(),
        _follow_up_fix(),
        _follow_up_haiku(),
    ]


def test_the_page_does_not_repeat_the_corrected_interval_claim() -> None:
    """The 3b report first said three results were "inside each other's intervals", and
    corrected it: they overlap. The page says neither."""
    assert "inside each other" not in " ".join(_chunks()).lower()
