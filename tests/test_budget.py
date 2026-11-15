"""The lab's Claude budget (ADR-037): one file, two readers that agree, and both spend ledgers
enforcing the per-item sub-caps, the project caps, the owner's approval and the paid window."""

import dataclasses
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_access.ai import budget as access_budget
from meridian_access.ai import spend as access_spend
from tier0.models import budget as tier0_budget
from tier0.models import spend as tier0_spend

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "budget" / "claude-credit-2026-12.json"
IN_WINDOW = datetime(2026, 12, 17, 23, 59, tzinfo=UTC)
# The items the owner has approved in the real file (ADR-037: one pull request per approval).
# aac-model-comparison (schedule item 1) was approved on 2026-11-15, ran once at k=3 for $2.02,
# and was set back to unapproved the same day so the rest of its cap can't be spent.
# tier0-3b (schedule item 2) was approved on 2026-11-15, spent $8.48 of its $15 on milestone
# 3b's tuning and paired run, and was set back to unapproved the same day (ADR-039).
APPROVED: set[str] = set()
AFTER = datetime(2026, 12, 18, 0, 0, tzinfo=UTC)

LEDGERS = {
    "tier0": (tier0_spend.SpendLedger, tier0_spend.SpendCapReached, tier0_budget, "tier0-3b"),
    "access": (
        access_spend.SpendLedger,
        access_spend.SpendCapReached,
        access_budget,
        "aac-red-team",
    ),
}


def test_the_budget_file_splits_the_credit_as_the_owner_decided():
    raw = json.loads(FILE.read_text(encoding="utf-8"))
    assert raw["credit_usd"] == 100
    assert raw["projects"] == {"tier0": 55, "access": 30}
    assert raw["buffer_usd"] == 15
    assert raw["paid_until"] == "2026-12-17"
    assert raw["credit_expires"] == "2026-12-18T00:00:00Z"
    assert "oauth-radar" not in json.dumps(raw)  # Shadow AI Radar gets nothing from this credit
    b = tier0_budget.load_budget()
    assert sum(b.projects.values()) + b.buffer_usd == b.credit_usd
    # tier0's items in ADR-035's order: 3b, milestone 4, 3d, 3c.
    order = [i["id"] for i in raw["items"] if i["project"] == "tier0"]
    assert order == ["tier0-3b", "tier0-m4-knowledge", "tier0-3d", "tier0-3c"]
    assert b.project_items("access") == {
        "aac-model-comparison",
        "aac-m7-final-score",
        "aac-red-team",
    }


def test_only_the_items_the_owner_approved_are_approved():
    real = tier0_budget.load_budget()
    assert {i.id for i in real.items.values() if i.approved} == APPROVED


def test_the_two_readers_agree():
    t, a = tier0_budget.load_budget(), access_budget.load_budget()
    assert dataclasses.asdict(t) == dataclasses.asdict(a)


def test_the_measured_estimate_for_the_comparison_is_three_times_the_logged_run():
    """The Access compare run of 2026-11-15 (run 43703011539) logged $0.677 for k=1."""
    item = access_budget.load_budget().items["aac-model-comparison"]
    assert item.estimate_usd == pytest.approx(3 * 0.677, abs=0.005)


def test_the_adr_table_shows_the_file_s_numbers():
    text = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    adr = text[text.index("## ADR-037") :]
    b = tier0_budget.load_budget()
    for item in b.items.values():
        row = re.search(rf"^\| `{re.escape(item.id)}` \|.*$", adr, re.MULTILINE)
        assert row, item.id
        cells = [c.strip() for c in row.group(0).strip("|").split("|")]
        assert f"${item.estimate_usd:.2f}" in cells, (item.id, cells)
        assert f"${item.cap_usd:.0f}" in cells, (item.id, cells)


@pytest.mark.parametrize(
    "bad",
    [
        {"projects": {"tier0": 60, "access": 30}},  # items no longer add up to the project
        {"buffer_usd": 20},  # more than the credit
    ],
)
def test_a_budget_that_does_not_add_up_is_refused(tmp_path, bad):
    raw = {**json.loads(FILE.read_text(encoding="utf-8")), **bad}
    path = tmp_path / "b.json"
    path.write_text(json.dumps(raw))
    for module in (tier0_budget, access_budget):
        with pytest.raises(ValueError):
            module.load_budget(path)


def budget_with(module, approved=(), caps=None):
    """The real budget, with some items approved and some sub-caps changed (built in memory)."""
    real = module.load_budget()
    items = {
        i.id: dataclasses.replace(
            i, approved=i.id in approved, cap_usd=(caps or {}).get(i.id, i.cap_usd)
        )
        for i in real.items.values()
    }
    return dataclasses.replace(real, items=items)


def ledger(name, tmp_path, item, budget, clock=IN_WINDOW, **extra):
    cls, _, _, _ = LEDGERS[name]
    if name == "access":
        extra.setdefault("run_limit_usd", 100.0)
    return cls(tmp_path / f"{name}.jsonl", item=item, budget=budget, clock=lambda: clock, **extra)


@pytest.mark.parametrize("name", LEDGERS)
def test_nothing_is_paid_without_an_item_or_before_the_owner_approves_it(
    name, tmp_path, monkeypatch
):
    _, refused, module, item = LEDGERS[name]
    monkeypatch.delenv("TIER0_CLAUDE_BUDGET_ITEM", raising=False)
    monkeypatch.delenv("ACCESS_CLAUDE_BUDGET_ITEM", raising=False)
    unapproved = budget_with(module)
    with pytest.raises(refused, match="no budget item"):
        ledger(name, tmp_path, None, unapproved).check(0.01)
    with pytest.raises(refused, match="not approved"):
        ledger(name, tmp_path, item, unapproved).check(0.01)
    # Items not approved in the real file are refused. Which items are approved is pinned
    # below, so each approval is a deliberate, reviewed change.
    real = module.load_budget()
    for item_id in real.project_items(name) - APPROVED:
        with pytest.raises(refused, match="not approved"):
            ledger(name, tmp_path, item_id, real).check(0.0)


@pytest.mark.parametrize("name", LEDGERS)
def test_an_item_of_the_other_project_or_an_unknown_one_is_refused(name, tmp_path):
    _, refused, module, _ = LEDGERS[name]
    other = "aac-red-team" if name == "tier0" else "tier0-3b"
    b = budget_with(module, approved={other, "tier0-3b", "aac-red-team"})
    for item in (other, "no-such-item"):
        with pytest.raises(refused, match=r"is not an? "):
            ledger(name, tmp_path, item, b).check(0.01)


@pytest.mark.parametrize("name", LEDGERS)
def test_an_approved_item_pays_until_the_end_of_2026_12_17_and_no_later(name, tmp_path):
    _, refused, module, item = LEDGERS[name]
    b = budget_with(module, approved={item})
    led = ledger(name, tmp_path, item, b, clock=IN_WINDOW)
    led.check(0.01)
    led.record("claude-haiku-5-5", 1000, 100)
    entry = json.loads((tmp_path / f"{name}.jsonl").read_text().splitlines()[0])
    assert entry["budget_item"] == item
    with pytest.raises(refused, match="paid window closed on 2026-12-17"):
        ledger(name, tmp_path, item, b, clock=AFTER).check(0.0)


@pytest.mark.parametrize("name", LEDGERS)
def test_the_item_sub_cap_stops_the_call_that_would_cross_it(name, tmp_path):
    _, refused, module, item = LEDGERS[name]
    b = budget_with(module, approved={item}, caps={item: 1.0})
    led = ledger(name, tmp_path, item, b)
    led.record("claude-sonnet-5-5", 0, 99_000)  # $0.99 on this item
    led.check(0.01)  # exactly to the cap is allowed
    with pytest.raises(refused, match=f"budget item {item}"):
        led.check(0.011)


@pytest.mark.parametrize("name", LEDGERS)
def test_the_project_cap_counts_every_item_of_the_project(name, tmp_path):
    _, refused, module, item = LEDGERS[name]
    b = budget_with(module, approved=set(module.load_budget().items))
    mine = sorted(b.project_items(name) - {item})
    led_other = ledger(name, tmp_path, mine[0], b)
    # Fill the project to $0.50 under its cap with another item's calls.
    target = b.projects[name] - b.items[item].cap_usd + (b.items[item].cap_usd - 0.5)
    tokens = int(target / 20e-6)  # Opus output at $20 per million
    led_other.record("claude-opus-5-5", 0, tokens)
    led = ledger(name, tmp_path, item, b)
    led.check(0.4)  # the item has room and so does the project
    with pytest.raises(refused, match=f"the {name} budget"):
        led.check(0.6)


@pytest.mark.parametrize("name", LEDGERS)
def test_spend_from_before_the_budget_counts_against_no_item(name, tmp_path):
    _, _, module, item = LEDGERS[name]
    path = tmp_path / f"{name}.jsonl"
    old = {"at": "2026-11-12T19:00:00+00:00", "model": "claude-sonnet-5-5"}
    path.write_text(json.dumps({**old, "input_tokens": 0, "output_tokens": 0, "cost_usd": 999}))
    b = budget_with(module, approved={item})
    ledger(name, tmp_path, item, b).check(1.0)


def test_the_access_run_limit_still_applies_inside_the_budget(tmp_path):
    _, refused, module, item = LEDGERS["access"]
    b = budget_with(module, approved={item})
    led = ledger("access", tmp_path, item, b, run_limit_usd=0.01)
    led.record("claude-sonnet-5-5", 0, 900)  # $0.009 this run
    with pytest.raises(refused, match="per-run limit"):
        led.check(0.002)


def test_no_code_outside_the_tests_sets_a_fixed_cap():
    """Only tests may build a ledger with one fixed cap; real runs always use the budget."""
    found = subprocess.run(
        ["git", "grep", "-n", "-E", r"SpendLedger\([^)]*cap_usd", "--", "*/src/*"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert found == ""
    old_env = subprocess.run(
        ["git", "grep", "-n", "-E", "(TIER0|ACCESS)_CLAUDE_SPEND_CAP_USD", "--", "*/src/*"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert old_env == ""
