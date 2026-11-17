"""The four safety counts (AAC-005, M7-R14 (a)): 0 on the tuning world, each one able to fail,
and in the final run's report with target 0, as counts only.

Fake clients only: no API call, no key, $0 spent. Tuning world only: the sealed world is never
built here (the end-to-end test puts the tuning world in its place).
"""

import ast
import dataclasses
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian_access import safety
from meridian_access.ai import final, frozen
from meridian_access.ai.budget import load_budget
from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.maps import load_entries
from meridian_access.ai.spend import SpendLedger
from meridian_access.collect import actual_state
from meridian_access.desired import load_hris
from meridian_access.diff import REMOVING
from meridian_access.match import Match, MatchResult
from meridian_access.models import Account
from meridian_access.plan import Plan

SRC = Path(safety.__file__)
BG = "breakglass-01@meridian.example"
DAY = "2026-11-20"


class FakeSDK:
    """Stands in for anthropic.Anthropic, as in test_final: records every request, answers
    with the first candidate (or the first role), never touches a network."""

    def __init__(self):
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.requests.append(params)
        answer = {}
        for name, prop in params["output_config"]["format"]["schema"]["properties"].items():
            if prop["type"] == "array":
                answer[name] = []
            elif "enum" in prop:
                answer[name] = prop["enum"][0]
            else:
                answer[name] = "A short plan with {total} changes."
        return SimpleNamespace(
            model=params["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=900, output_tokens=60),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))],
        )


def _ledger(tmp_path):
    return SpendLedger(tmp_path / "ledger.jsonl", cap_usd=3.0, run_limit_usd=2.0)


def _client(sdk, ledger):
    return frozen.FrozenClaudeClient(frozen.FROZEN_MODEL, client=sdk, ledger=ledger)


@pytest.fixture(scope="module")
def inputs(world_dir, policy):
    return final.build_inputs(world_dir, policy)


@pytest.fixture(scope="module")
def section(world_dir, inputs, policy):
    return final.safety_section(world_dir, inputs, policy)


# ------------------------------------------------------------------ the tuning world: 0


def test_the_four_counts_are_0_on_the_tuning_world_with_the_fake_client(keyed_world_dir, tmp_path):
    sdk, ledger = FakeSDK(), _ledger(tmp_path)
    out = tmp_path / "final"
    report = final.run_world(keyed_world_dir, out, _client(sdk, ledger), ledger)
    s = report["safety"]
    assert s["counts"] == {name: {"count": 0, "target": 0} for name in safety.NAMES}
    assert s["all_at_target"] is True
    # each 0 is a 0 of something
    assert all(n > 0 for n in s["checked"].values()), s["checked"]
    main, held = s["apply_dry_run"]["main"], s["apply_dry_run"]["held"]
    assert main["approvals"] == main["approvals_needed"] == 1 and main["merged"] is True
    assert main["applied"] > 0 and main["refused"] == ""
    assert held["approvals"] == 1 and held["approvals_needed"] == 2 and held["merged"] is False
    assert held["applied"] == 0 and held["refused"] == "not enough approvals"
    assert s["checked"]["changes_applied"] == main["applied"]
    # the fake model did suggest owners in this run, and still no removal rests on one
    assert report["scores"]["repeats"][0]["ai_matches"]["made"] > 0
    assert json.loads((out / "report.json").read_text())["safety"] == s


def test_the_held_part_applied_with_two_approvals_is_still_safe(world_dir, inputs, policy):
    """With its second approval the held part is merged and applied: its removals are over
    the limit, and none of them counts, because they have two approvals."""
    s = safety.measure(
        world_dir,
        inputs.plan,
        inputs.parts,
        policy,
        matches=inputs.work.result,
        map_entries=load_entries(),
        approvals={"main": 1, "held": 2},
    )
    assert all(v["count"] == 0 for v in s["counts"].values())
    held = s["apply_dry_run"]["held"]
    assert held["merged"] is True and held["applied"] > 0 and held["refused"] == ""
    assert (
        s["checked"]["changes_applied"] == s["apply_dry_run"]["main"]["applied"] + held["applied"]
    )


def test_the_counts_are_the_same_whatever_the_model_answered(section, keyed_world_dir, tmp_path):
    """The plan never reads an answer: the run's safety section is the one counted without
    any model."""
    sdk, ledger = FakeSDK(), _ledger(tmp_path)
    report = final.run_world(keyed_world_dir, tmp_path / "o", _client(sdk, ledger), ledger)
    assert report["safety"] == section


# --------------------------------------------------------- the real plan, made unsafe


def _api_removals(plan: Plan) -> tuple[dict, ...]:
    return tuple(
        c
        for c in plan.changes
        if c["action"] in REMOVING and c["system"] in ("okta", "google", "slack")
    )


def test_the_held_part_applied_with_one_approval_fails_two_counts(inputs, policy):
    """As if apply's approval check were gone: every held removal sent with one approval."""
    parts = dict(inputs.parts)
    sent = _api_removals(parts["held"])
    distinct = len({safety.identity(c) for c in sent})
    counts = safety.count(
        inputs.plan,
        [safety.Part("main", parts["main"], 1), safety.Part("held", parts["held"], 1, sent)],
        policy,
        matches=inputs.work.result,
    )
    assert distinct > 0
    assert counts.changes_without_merged_plan == distinct
    assert counts.removals_over_limit_without_two_approvals == distinct
    assert counts.break_glass_changes == counts.removals_on_unreviewed_ai_match == 0


def test_a_removal_of_a_break_glass_account_in_the_real_plan_counts(inputs, policy):
    parts = dict(inputs.parts)
    bad = {**_api_removals(parts["main"])[0], "account": BG, "employee_id": ""}
    main = Plan({**parts["main"].data, "changes": [*parts["main"].changes, bad]})
    counts = safety.count(
        inputs.plan, [safety.Part("main", main, 1)], policy, matches=inputs.work.result
    )
    assert counts.break_glass_changes == 1
    assert counts.changes_without_merged_plan == 0


def test_one_owner_link_swapped_for_an_ai_match_in_the_real_plan_counts(inputs, policy):
    first = next(c for c in inputs.plan.changes if c["action"] in REMOVING and c["employee_id"])
    rules = inputs.work.result
    key = next(
        k
        for k in rules.matches
        if (k[0], k[1], k[2].strip().casefold())
        == (first["system"], first["app_id"], first["account"].casefold())
    )
    swapped = MatchResult(
        {**rules.matches, key: Match(first["employee_id"], "ai", 0.5)},
        rules.unmatched,
        rules.ambiguous,
    )
    resting = {
        safety.identity(c)
        for c in [*inputs.plan.changes, *inputs.plan.data["held"]]
        if c["action"] in REMOVING
        and c["employee_id"]
        and (c["system"], c["app_id"], c["account"].casefold())
        == (key[0], key[1], first["account"].casefold())
    }
    counts = safety.count(
        inputs.plan, [safety.Part("main", dict(inputs.parts)["main"], 1)], policy, matches=swapped
    )
    assert counts.removals_on_unreviewed_ai_match == len(resting) >= 1


# ------------------------------------------------------------ small cases: each can fail


def _c(
    account,
    action="remove",
    *,
    type_="leaver_access_left",
    employee="E1",
    system="okta",
    app_id="",
    day=DAY,
    hold="",
):
    return {
        "type": type_,
        "employee_id": employee,
        "account": account,
        "system": system,
        "app_id": app_id,
        "app_name": "",
        "action": action,
        "reason": "",
        "evidence": [],
        "cause_date": day,
        "hold": hold,
    }


def _plan(changes=(), held=(), part="main", approvals_required=1):
    return Plan(
        {
            "version": 2,
            "part": part,
            "changes": list(changes),
            "held": list(held),
            "approvals_required": approvals_required,
        }
    )


def _rules(*changes):
    """A rule match (exact email) from each change's account to its owner, so only the count
    under test can fire."""
    return MatchResult(
        {
            (c["system"], c["app_id"], c["account"]): Match(c["employee_id"], "exact_email", 1.0)
            for c in changes
            if c["employee_id"]
        },
        [],
        [],
    )


def _count(combined, parts, policy, matches=None, **kw):
    every = [*combined.changes, *combined.data["held"], *(c for p in parts for c in p.applied)]
    return safety.count(
        combined, parts, policy, matches=matches if matches is not None else _rules(*every), **kw
    )


def _only(counts, name, n):
    values = counts.counts()
    assert values[name] == n, values
    assert all(v == 0 for k, v in values.items() if k != name), values


@pytest.mark.parametrize(
    "account",
    [BG, " BreakGlass-01@Meridian.EXAMPLE ", "breakglass-02", "breakglass-77@meridian.example"],
    ids=["listed", "case-and-spaces", "bare-username", "pattern"],
)
def test_a_planned_change_on_a_break_glass_account_counts(policy, account):
    plan = _plan([_c(account, employee="")])
    _only(_count(plan, [safety.Part("main", plan, 1)], policy), "break_glass_changes", 1)


def test_every_name_of_a_break_glass_account_is_break_glass(policy):
    alias = "ops-root@meridian.example"
    accounts = [Account("google", BG, email=BG, aliases=(alias,))]
    plan = _plan([_c(alias, "suspend", system="google", employee="")])
    parts = [safety.Part("main", plan, 1)]
    assert _count(plan, parts, policy).break_glass_changes == 0  # not on the list by itself
    _only(_count(plan, parts, policy, accounts=accounts), "break_glass_changes", 1)


def test_a_break_glass_change_planned_and_applied_counts_once(policy):
    change = _c(BG, employee="")
    plan = _plan([change])
    counts = _count(plan, [safety.Part("main", plan, 1, (change,))], policy)
    _only(counts, "break_glass_changes", 1)


def test_a_break_glass_change_held_or_only_applied_counts(policy):
    held_row = _c(BG, employee="", hold="bad_data")
    plan = _plan([], held=[held_row])
    _only(_count(plan, [safety.Part("main", plan, 1)], policy), "break_glass_changes", 1)
    sent = _c("breakglass-03@meridian.example", employee="")
    empty = _plan([])
    counts = _count(empty, [safety.Part("main", empty, 1, (sent,))], policy)
    assert counts.break_glass_changes == 1
    assert counts.changes_without_merged_plan == 1  # it is not in the plan either


def test_the_report_only_entry_for_a_break_glass_account_is_not_a_change(policy):
    entry = _c(BG, "never_touch", type_="break_glass", employee="", day="")
    plan = _plan([entry])
    counts = _count(plan, [safety.Part("main", plan, 1)], policy)
    assert counts.counts() == dict.fromkeys(safety.NAMES, 0)
    assert counts.changes_planned == 0


def test_changes_applied_from_a_held_part_without_two_approvals_count(policy):
    a, b = _c("a@meridian.example", employee="E1"), _c("b@meridian.example", employee="E2")
    combined = _plan([a, b])
    held = _plan([a, b], part="held", approvals_required=2)
    # two people on one day: under the limit, so only the merged-plan count fires
    counts = _count(combined, [safety.Part("held", held, 1, (a, b))], policy)
    _only(counts, "changes_without_merged_plan", 2)
    counts = _count(combined, [safety.Part("held", held, 2, (a, b))], policy)
    assert counts.counts() == dict.fromkeys(safety.NAMES, 0)


def test_a_held_plan_that_says_it_needs_one_approval_still_needs_two(policy):
    a = _c("a@meridian.example")
    held = _plan([a], part="held", approvals_required=1)
    assert safety.approvals_needed("held", held, policy) == 2
    _only(
        _count(_plan([a]), [safety.Part("held", held, 1, (a,))], policy),
        "changes_without_merged_plan",
        1,
    )


def test_a_change_applied_that_its_plan_does_not_list_counts(policy):
    listed, extra = _c("a@meridian.example"), _c("x@meridian.example", employee="E9")
    main = _plan([listed])
    counts = _count(main, [safety.Part("main", main, 1, (listed, extra))], policy)
    _only(counts, "changes_without_merged_plan", 1)


def test_a_part_of_unknown_name_is_never_merged(policy):
    a = _c("a@meridian.example")
    plan = _plan([a], part="extra")
    _only(
        _count(plan, [safety.Part("extra", plan, 5, (a,))], policy),
        "changes_without_merged_plan",
        1,
    )


def _people(n, day=DAY, hold=""):
    return [_c(f"p{i}@meridian.example", employee=f"E{i}", day=day, hold=hold) for i in range(n)]


@pytest.mark.parametrize("day", [DAY, ""], ids=["one-day", "no-date"])
def test_removals_over_the_people_limit_applied_with_one_approval_count(policy, day):
    n = policy.settings.blast_radius_people  # the limit is "n or more people"
    rows = _people(n, day=day)
    plan = _plan(rows)
    counts = _count(plan, [safety.Part("main", plan, 1, tuple(rows))], policy)
    assert counts.removals_over_limit_without_two_approvals == n
    assert counts.changes_without_merged_plan == n  # a part like that needs two to merge
    counts = _count(plan, [safety.Part("main", plan, 2, tuple(rows))], policy)
    assert counts.counts() == dict.fromkeys(safety.NAMES, 0)


def test_removals_under_the_limit_do_not_count(policy):
    rows = _people(policy.settings.blast_radius_people - 1)
    plan = _plan(rows)
    counts = _count(plan, [safety.Part("main", plan, 1, tuple(rows))], policy)
    assert counts.counts() == dict.fromkeys(safety.NAMES, 0)
    assert counts.removals_over_the_limit == 0


def test_removals_over_the_removals_limit_count(policy):
    n = policy.settings.blast_radius_removals
    rows = [_c("p@meridian.example", app_id=f"app{i}") for i in range(n)]  # one person
    plan = _plan(rows)
    counts = _count(plan, [safety.Part("main", plan, 1, tuple(rows))], policy)
    assert counts.removals_over_limit_without_two_approvals == n


def test_a_bucket_split_across_the_two_parts_is_still_over_the_limit(policy):
    rows = _people(policy.settings.blast_radius_people)
    first, rest = rows[:3], rows[3:]
    main = _plan(first)  # on its own, 3 people: one approval is all it asks for
    held = _plan(rest, part="held", approvals_required=2)
    parts = [safety.Part("main", main, 1, tuple(first)), safety.Part("held", held, 1)]
    _only(_count(_plan(rows), parts, policy), "removals_over_limit_without_two_approvals", 3)


def test_removals_applied_that_no_plan_lists_are_in_the_buckets_too(policy):
    rows = _people(policy.settings.blast_radius_people)
    empty = _plan([])
    counts = _count(empty, [safety.Part("main", empty, 1, tuple(rows))], policy)
    assert counts.removals_over_limit_without_two_approvals == len(rows)
    assert counts.changes_without_merged_plan == len(rows)


def test_a_bulk_import_removal_applied_with_one_approval_counts(policy):
    row = _c("p@meridian.example", hold="bulk")
    cleared = {**row, "hold": ""}
    combined = _plan([], held=[row])
    held = _plan([cleared], part="held", approvals_required=2)
    counts = _count(combined, [safety.Part("held", held, 1, (cleared,))], policy)
    assert counts.removals_over_limit_without_two_approvals == 1
    assert counts.changes_without_merged_plan == 1
    counts = _count(combined, [safety.Part("held", held, 2, (cleared,))], policy)
    assert counts.counts() == dict.fromkeys(safety.NAMES, 0)


def test_only_removals_and_suspensions_are_over_the_limit(policy):
    rows = [{**r, "action": "flag"} for r in _people(policy.settings.blast_radius_people)]
    plan = _plan(rows)
    counts = _count(plan, [safety.Part("main", plan, 1, tuple(rows))], policy)
    assert counts.removals_over_limit_without_two_approvals == 0
    assert counts.removals_over_the_limit == 0


KEY = ("okta", "", "p@meridian.example")
AI = MatchResult({KEY: Match("E1", "ai", 0.5)}, [], [])
RULE = MatchResult({KEY: Match("E1", "exact_email", 1.0)}, [], [])
OTHER = MatchResult({KEY: Match("E2", "exact_email", 1.0)}, [], [])
REVIEW_MAP = MatchResult({KEY: Match("E1", "reviewed_map", 1.0)}, [], [])
NONE = MatchResult({}, [KEY], [])


def _entry(value="E1", reviewed=True):
    return {
        "kind": "account",
        "key": "okta//p@meridian.example",
        "value": value,
        "source": "ai",
        "reviewed": reviewed,
    }


@pytest.mark.parametrize(
    ("matches", "entries", "expected"),
    [
        (AI, [], 1),
        (AI, [_entry(reviewed=False)], 1),
        (AI, [_entry()], 0),
        (AI, [_entry("E2")], 1),
        (NONE, [], 1),
        (NONE, [_entry()], 0),
        (REVIEW_MAP, [], 1),
        (REVIEW_MAP, [_entry()], 0),
        (RULE, [], 0),
        (OTHER, [], 1),
    ],
    ids=[
        "ai-match",
        "ai-match-unreviewed-entry",
        "ai-match-reviewed-entry",
        "reviewed-entry-for-another-person",
        "no-match",
        "reviewed-entry-only",
        "reviewed-rule-without-an-entry",
        "reviewed-rule-with-its-entry",
        "rule-match",
        "rule-match-to-another-person",
    ],
)
def test_a_removal_counts_unless_a_rule_or_a_reviewed_entry_backs_its_owner(
    policy, matches, entries, expected
):
    plan = _plan([_c(" P@Meridian.example")])  # case and spaces don't hide the link
    counts = _count(plan, [safety.Part("main", plan, 1)], policy, matches, map_entries=entries)
    assert counts.removals_on_unreviewed_ai_match == expected
    assert counts.removals_with_an_owner == 1


def test_an_applied_removal_on_an_ai_match_counts_once(policy):
    row = _c("p@meridian.example")
    plan = _plan([row])
    counts = _count(plan, [safety.Part("main", plan, 1, (row,))], policy, AI)
    _only(counts, "removals_on_unreviewed_ai_match", 1)


@pytest.mark.parametrize(
    "row",
    [
        _c("p@meridian.example", "flag"),
        _c("p@meridian.example", "add", type_="mover_stale_access"),
        _c("p@meridian.example", type_="orphaned_account", employee=""),
    ],
    ids=["flag", "add", "orphan-with-no-owner"],
)
def test_only_a_removal_with_an_owner_rests_on_a_match(policy, row):
    plan = _plan([row])
    counts = _count(plan, [safety.Part("main", plan, 1)], policy, AI)
    assert counts.removals_on_unreviewed_ai_match == 0


def test_two_parts_with_one_name_are_refused(policy):
    plan = _plan([])
    with pytest.raises(ValueError, match="two parts"):
        _count(plan, [safety.Part("main", plan, 1), safety.Part("main", plan, 1)], policy)


# ----------------------------------------------------------------------- the report


ALLOWED_STRINGS = {"", *safety.REFUSALS, "main", "held"}


def _strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def test_the_safety_section_holds_counts_and_fixed_words_only(section):
    assert list(section["counts"]) == list(safety.NAMES)
    assert all(v["target"] == 0 for v in section["counts"].values())
    keys = {"count", "target", "counts", "all_at_target", "checked", "apply_dry_run"}
    keys |= {*safety.NAMES, *section["checked"], *section["apply_dry_run"]["main"]}
    for s in _strings(section):
        assert s in keys | ALLOWED_STRINGS, s
    assert "@" not in json.dumps(section)


def test_the_counts_are_apart_from_precision_and_recall(keyed_world_dir, tmp_path):
    sdk, ledger = FakeSDK(), _ledger(tmp_path)
    report = final.run_world(keyed_world_dir, tmp_path / "o", _client(sdk, ledger), ledger)
    assert "safety" in report and "safety" not in report["scores"]
    assert not any("precision" in k or "recall" in k for k in _strings(report["safety"]))


def test_the_log_lines_show_each_count_with_its_target(policy):
    counts = safety.Counts(1, 2, 3, 4, 10, 10, 10, 10)
    lines = safety.log_lines(safety.section(counts, [], policy))
    assert lines == [
        "  safety, changes to break-glass accounts: 1 (target 0)",
        "  safety, changes without a merged plan: 2 (target 0)",
        "  safety, removals over the limit without two approvals: 3 (target 0)",
        "  safety, removals resting on an unreviewed AI match: 4 (target 0)",
    ]


def _budget_with_the_item_approved():
    b = load_budget()
    items = dict(b.items)
    items[final.BUDGET_ITEM] = dataclasses.replace(items[final.BUDGET_ITEM], approved=True)
    return dataclasses.replace(b, items=items)


def _world_names(world_dir, policy) -> set[str]:
    """Every account name, username and email of the tuning world, and every person's name."""
    names: set[str] = set()
    for a in actual_state(world_dir, policy).accounts:
        names |= {a.account, a.email, a.display, *a.aliases}
    records, _ = load_hris(world_dir / "hris.json")
    for r in records:
        names |= {r.work_email, f"{r.given_name} {r.family_name}"}
    return {n.strip().casefold() for n in names if len(n.strip()) >= 4}


def test_the_final_command_prints_the_four_counts_and_no_names_or_emails(
    keyed_world_dir, world_dir, policy, monkeypatch, tmp_path, capsys
):
    """Every guard passed on a copy of the budget with the item approved, and the tuning world
    standing in for the sealed one; the calls go to a fake SDK."""

    def factory(item):
        return SpendLedger(
            tmp_path / "ledger.jsonl",
            item=item,
            budget=_budget_with_the_item_approved(),
            clock=lambda: datetime(2026, 11, 20, 12, tzinfo=UTC),  # inside the paid window
            run_limit_usd=2.0,
        )

    def stand_in(name, target):
        assert name == "held_out_a1"
        shutil.copytree(keyed_world_dir, target)
        return target

    sdk = FakeSDK()
    monkeypatch.setattr(final, "SpendLedger", factory)
    monkeypatch.setattr(final, "build_sealed_world", stand_in)
    monkeypatch.setattr(
        ClaudeClient, "_sdk_client", lambda self: self._client if self._client else sdk
    )
    out = tmp_path / "final-out"
    assert (
        final.final_main("held_out_a1", out, k=1, summaries=True, env={"ACCESS_AI_LIVE": "1"}) == 0
    )
    printed = capsys.readouterr().out
    for label in safety.LABELS.values():
        assert f"  safety, {label}: 0 (target 0)\n" in printed
    assert "@" not in printed
    low = printed.casefold()
    names = _world_names(world_dir, policy)
    assert len(names) > 100  # the check below has something to look for
    leaked = sorted(n for n in names if n in low)
    assert leaked == []
    report = json.loads((out / "report.json").read_text())
    assert report["safety"]["counts"] == {n: {"count": 0, "target": 0} for n in safety.NAMES}


def test_the_counts_need_no_model_and_no_network():
    tree = ast.parse(SRC.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = ("anthropic", "meridian_access.ai", "urllib", "http", "socket", "requests", "httpx")
    assert not [m for m in imported if m.split(".")[0] in banned or m.startswith(banned)]
