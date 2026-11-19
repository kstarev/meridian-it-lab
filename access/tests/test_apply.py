"""Apply: dry run by default, mock-live only on the mocks, stale and unapproved plans refused,
break-glass never changed, idempotent, safe under 429/5xx. Tuning world only; no network."""

import ast
import copy
import dataclasses
import json
import re
import shutil
from pathlib import Path

import pytest

from meridian_access import apply as apply_mod
from meridian_access import slack_mock
from meridian_access.apply import (
    ACCEPTED_MODES,
    apply_plan,
    confirm_manual_tasks,
    contradictions,
    issue_write_tokens,
    normalise_mode,
)
from meridian_access.approvals import required_for
from meridian_access.collect import Mocks, actual_state
from meridian_access.mocks import GoogleMock, MockResponse, OktaMock, SlackMock
from meridian_access.plan import Plan, build_plan, split_plan
from meridian_access.policy.load import default_policy_dir, load_policy

SRC = Path(apply_mod.__file__)
NETWORK = {"urllib", "http", "socket", "requests", "httpx", "slack_sdk", "ssl", "aiohttp"}
BG = "breakglass-01@meridian.example"


@pytest.fixture(scope="module")
def combined(world_dir, policy) -> Plan:
    return build_plan(world_dir, policy)


@pytest.fixture(scope="module")
def parts(combined, policy) -> tuple[Plan, Plan]:
    main, held = split_plan(combined, policy)
    assert held is not None
    return main, held


@pytest.fixture(scope="module")
def plan(parts) -> Plan:
    """The main part of the tuning-world plan (what the apply workflow applies)."""
    return parts[0]


@pytest.fixture(scope="module")
def everything(combined) -> Plan:
    """Main and held changes in one hand-made plan, for tests that need every kind of change
    (drift, orphans, mover adds, leavers). It is applied with `rebuild=lambda state: everything`:
    the real split would never produce it."""
    return Plan({**combined.data, "part": "main", "approvals_required": 2})


def make_mocks(world_dir) -> Mocks:
    return Mocks(
        OktaMock.from_files(world_dir, fault_rate=0.0),
        GoogleMock.from_files(world_dir, fault_rate=0.0),
        SlackMock.from_files(world_dir, fault_rate=0.0),
    )


def snapshot(mocks: Mocks) -> dict:
    return copy.deepcopy(
        {"okta": mocks.okta.data, "google": mocks.google.data, "slack": mocks.slack.data}
    )


def run(plan, world_dir, policy, mocks=None, *, mode="mock-live", approvals=2, **kw):
    mocks = mocks or make_mocks(world_dir)
    tokens = issue_write_tokens(mocks)
    result = apply_plan(
        plan,
        mocks,
        mode=mode,
        approvals=approvals,
        policy=policy,
        write_token=tokens,
        sleep=lambda s: None,
        world_dir=world_dir,
        **kw,
    )
    return result, mocks


def run_hand(hand, world_dir, policy, mocks, **kw):
    """Apply a hand-made plan: the rebuild step (which returns the fresh plan of the same part)
    returns that same plan, because the diff would never produce the changes a guard test
    needs (the guard is what is under test)."""
    return run(hand, world_dir, policy, mocks, rebuild=lambda state: hand, **kw)


def api_changes(plan):
    """Changes apply sends to a mock: suspend, remove and add for the three directories."""
    return [
        c
        for c in plan.changes
        if c["system"] in ("okta", "google", "slack")
        and c["action"] in ("suspend", "remove", "add")
    ]


def replanned(plan: Plan, mocks, world_dir, policy) -> Plan:
    """The same changes against the refreshed state: what a re-plan would record as its hash."""
    state = actual_state(mocks, policy, world_dir / "exports")
    return Plan({**plan.data, "actual_state_sha256": state.digest()})


def hand_plan(mocks, world_dir, policy, changes, approvals_required=1) -> Plan:
    state = actual_state(mocks, policy, world_dir / "exports")
    return Plan(
        {
            "version": 2,
            "part": "main",
            "as_of": "2026-12-01",
            "actual_state_sha256": state.digest(),
            "changes": changes,
            "held": [],
            "warnings": [],
            "manual_tasks": [],
            "approvals_required": approvals_required,
        }
    )


def change(system, action, account, app_id="", type_="leaver_access_left"):
    return {
        "type": type_,
        "employee_id": "E0",
        "account": account,
        "system": system,
        "app_id": app_id,
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": "",
    }


# ---------------------------------------------------------------- modes


def test_the_accepted_modes_are_exactly_dry_run_and_mock_live():
    assert {"dry-run", "mock-live"} == ACCEPTED_MODES
    assert normalise_mode("mock-live") == "mock-live"
    for other in (None, "", "dry-run", "live", "real", "production", "Mock-Live", "mock-live "):
        assert normalise_mode(other) == "dry-run"


def test_a_dry_run_says_what_it_would_do_and_changes_nothing(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks, mode="dry-run")
    assert result.mode == "dry-run" and not result.refused_reason
    assert len(result.applied) == len(api_changes(plan))
    assert all(line for line in result.log) and any("would:" in line for line in result.log)
    assert snapshot(mocks) == before


@pytest.mark.parametrize("mode", [None, "", "live", "real", "production", "MOCK-LIVE"])
def test_an_unknown_mode_is_a_dry_run_never_live(plan, world_dir, policy, mode):
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks, mode=mode)
    assert result.mode == "dry-run"
    assert snapshot(mocks) == before


def test_mock_live_changes_the_mocks(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks)
    assert result.mode == "mock-live" and not result.refused_reason and not result.failed
    assert len(result.applied) == len(api_changes(plan))
    after = snapshot(mocks)
    assert after != before
    assert len(after["okta"]["users"]) == len(before["okta"]["users"])  # nobody is deleted
    assert len(after["google"]["users"]) == len(before["google"]["users"])
    assert len(after["okta"]["assignments"]) < len(before["okta"]["assignments"])


def test_apply_has_no_write_path_without_tokens_and_none_to_real_systems(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=2,
        policy=policy,
        write_token=None,
        world_dir=world_dir,
    )
    assert result.refused_reason == "no write token"
    assert snapshot(mocks) == before
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & NETWORK
    assert "answer_key" not in SRC.read_text(encoding="utf-8")


# ---------------------------------------------------------------- refusals


def test_a_stale_plan_is_refused_and_nothing_changes(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    token = mocks.google.issue_token("someone", {"read", "write"})
    user = mocks.google.data["users"][0]
    mocks.google.request(
        "PATCH",
        f"/admin/directory/v1/users/{user['id']}",
        token=token,
        body={"suspended": not user["suspended"]},
    )
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks)
    assert result.refused_reason == "stale"
    assert not result.applied and snapshot(mocks) == before


def test_a_dry_run_also_refuses_a_stale_plan(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    mocks.slack.data["Resources"][0]["active"] = not mocks.slack.data["Resources"][0]["active"]
    result, _ = run(plan, world_dir, policy, mocks, mode="dry-run")
    assert result.refused_reason == "stale"


def test_missing_approvals_are_refused_and_nothing_changes(parts, world_dir, policy):
    held = parts[1]
    assert held.data["part"] == "held" and held.data["approvals_required"] == 2
    for have in (0, 1):
        mocks = make_mocks(world_dir)
        before = snapshot(mocks)
        result, _ = run(held, world_dir, policy, mocks, approvals=have)
        assert result.refused_reason == "needs 2 approvals"
        assert not result.applied and snapshot(mocks) == before
    ok, _ = run(held, world_dir, policy, approvals=2, mode="dry-run")
    assert not ok.refused_reason and ok.applied


def test_the_main_part_with_one_approval_passes_the_approvals_step(plan, world_dir, policy):
    assert plan.data["part"] == "main" and plan.data["approvals_required"] == 1
    result, _ = run(plan, world_dir, policy, approvals=1, mode="dry-run")
    assert not result.refused_reason and result.applied
    zero, _ = run(plan, world_dir, policy, approvals=0, mode="dry-run")
    assert zero.refused_reason == "needs 1 approvals"


def test_moving_a_change_from_the_held_plan_into_main_is_refused(parts, world_dir, policy):
    main, held = parts
    moved = next(c for c in held.changes if c["action"] in ("remove", "suspend"))
    data = copy.deepcopy(main.data)
    data["changes"].append(moved)
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    # One extra removal in main does not raise main's requirement (one person on one day, far
    # below the limits), so approvals are not what stops it: one approval gets past that step,
    # and the rebuild comparison refuses the plan because the fresh main plan lacks the change.
    assert required_for(data["changes"], policy) == 1
    result, _ = run(Plan(data), world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "plan changed"
    assert not result.applied and snapshot(mocks) == before
    result, _ = run(Plan(data), world_dir, policy, make_mocks(world_dir), approvals=2)
    assert result.refused_reason == "plan changed"


def test_a_held_plan_relabelled_main_with_one_approval_is_refused(parts, world_dir, policy):
    _, held = parts
    data = {**copy.deepcopy(held.data), "part": "main", "approvals_required": 1}
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    # the changes still need two (bucket limits), whatever the label and the claim say
    result, _ = run(Plan(data), world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals"
    assert not result.applied and snapshot(mocks) == before
    # with two approvals it is a main plan that differs from the fresh main plan
    result, _ = run(Plan(data), world_dir, policy, make_mocks(world_dir), approvals=2)
    assert result.refused_reason == "plan changed"


def test_a_main_plan_relabelled_held_is_refused(parts, world_dir, policy):
    main, _ = parts
    data = {**copy.deepcopy(main.data), "part": "held"}
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    # the held floor: needs two approvals although main's changes need one
    result, _ = run(Plan(data), world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals"
    assert not result.applied and snapshot(mocks) == before
    # with two it is compared with the fresh held plan and differs
    result, _ = run(Plan(data), world_dir, policy, make_mocks(world_dir), approvals=2)
    assert result.refused_reason == "plan changed"


def test_the_held_part_needs_two_approvals_whatever_the_file_and_the_changes_say(world_dir, policy):
    mocks = make_mocks(world_dir)
    hand = hand_plan(mocks, world_dir, policy, [], approvals_required=1)
    held = Plan({**hand.data, "part": "held"})
    result, _ = run_hand(held, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals"
    ok, _ = run_hand(held, world_dir, policy, make_mocks(world_dir), approvals=2)
    assert not ok.refused_reason


def test_applying_one_part_makes_the_other_stale(parts, world_dir, policy):
    main, held = parts
    assert main.data["actual_state_sha256"] == held.data["actual_state_sha256"]
    mocks = make_mocks(world_dir)
    first, _ = run(main, world_dir, policy, mocks, approvals=1)
    assert not first.refused_reason and first.applied
    before = snapshot(mocks)
    second, _ = run(held, world_dir, policy, mocks, approvals=2)
    assert second.refused_reason == "stale"
    assert not second.applied and snapshot(mocks) == before
    # and the other way round
    mocks = make_mocks(world_dir)
    first, _ = run(held, world_dir, policy, mocks, approvals=2)
    assert not first.refused_reason and first.applied
    second, _ = run(main, world_dir, policy, mocks, approvals=1)
    assert second.refused_reason == "stale"


def test_a_plan_with_a_missing_or_unknown_part_is_refused(plan, world_dir, policy):
    for part in (None, "", "both", "Main", 1):
        data = copy.deepcopy(plan.data)
        if part is None:
            del data["part"]
        else:
            data["part"] = part
        mocks = make_mocks(world_dir)
        before = snapshot(mocks)
        result, _ = run(Plan(data), world_dir, policy, mocks, approvals=2)
        assert "unknown plan part" in result.refused_reason
        assert not result.applied and snapshot(mocks) == before


def test_a_part_the_fresh_split_does_not_have_is_plan_changed(
    parts, world_dir, policy, monkeypatch
):
    main, held = parts
    # the fresh build has no held plan any more (for example the bulk import was corrected)
    monkeypatch.setattr(apply_mod, "split_plan", lambda plan, pol: (main, None))
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(held, world_dir, policy, mocks, approvals=2)
    assert result.refused_reason.startswith("plan changed")
    assert "no held plan" in result.refused_reason and snapshot(mocks) == before


def test_a_state_that_cannot_be_read_is_a_refusal_not_a_change(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    mocks.okta.fault_rate = 1.0
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks)
    assert result.refused_reason.startswith("cannot read the current state")
    assert snapshot(mocks) == before


def test_held_changes_are_never_applied(plan, world_dir, policy):
    assert plan.data["held"]
    held_accounts = {(c["system"], c["app_id"], c["account"]) for c in plan.data["held"]}
    applied = {(c["system"], c["app_id"], c["account"]) for c in plan.changes}
    assert not held_accounts & applied  # the plan keeps them apart
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, policy, mocks)
    bad = [c for c in plan.data["held"] if c["hold"] == "bad_data"]
    assert result.held_not_applied == len(bad)  # moved changes are in the held plan, not "never"
    held_okta_users = {a for s, app, a in held_accounts if s == "okta" and not app} - {
        a for s, app, a in applied if s == "okta" and not app
    }
    status = {u["profile"]["login"]: u["status"] for u in mocks.okta.data["users"]}
    was = {u["profile"]["login"]: u["status"] for u in before["okta"]["users"]}
    for login in held_okta_users:
        assert status[login] == was[login]


# ---------------------------------------------------------------- break-glass


def write_calls(mocks):
    """Count the writes (non-GET requests) made to each mock from now on."""
    seen = []
    for mock in (mocks.okta, mocks.google, mocks.slack):
        original = mock.request

        def spy(method, path, *, _o=original, **kw):
            if method != "GET":
                seen.append((method, path))
            return _o(method, path, **kw)

        mock.request = spy
    return seen


def test_no_change_type_ever_touches_a_break_glass_account(world_dir, policy):
    mocks = make_mocks(world_dir)
    slack_user = next(u["userName"] for u in mocks.slack.data["Resources"] if u["active"])
    listed = policy.break_glass_accounts | {slack_user}
    strict = dataclasses.replace(policy, break_glass_accounts=frozenset(listed))
    okta_apps = {
        a["name"]
        for a in mocks.okta.data["apps"]
        if any(
            x["appId"] == a["id"] and _login(mocks, x["userId"]) == BG
            for x in mocks.okta.data["assignments"]
        )
    }
    app_id = sorted(okta_apps)[0] if okta_apps else "github"
    names = {
        "okta": [BG, BG.upper(), f"  {BG}  ", "breakglass-02@meridian.example"],
        "google": [BG, "breakglass-03@meridian.example"],
        "slack": [slack_user],
    }
    # only changes that would make an API call: every system's suspend and remove, an Okta app
    # unassignment and an Okta app add
    takes_away = []
    for action in ("suspend", "remove"):
        for system, accounts in names.items():
            takes_away += [change(system, action, account) for account in accounts]
    takes_away += [change("okta", "remove", account, app_id) for account in names["okta"]]
    adds = [change("okta", "add", account, app_id) for account in names["okta"]]
    # a plan that adds and removes one target is refused whole (AAC-017): two plans, so each
    # change reaches the guard
    for changes in (takes_away, adds):
        bad = hand_plan(mocks, world_dir, strict, changes, approvals_required=1)
        before = snapshot(mocks)
        writes = write_calls(mocks)
        result, _ = run_hand(bad, world_dir, strict, mocks, approvals=1)
        assert not result.refused_reason, result.refused_reason
        assert writes == [] and snapshot(mocks) == before
        assert result.applied == [] and result.skipped_already_done == [] and result.failed == []
        assert len(result.dropped_break_glass) == len(changes)
        assert sum(1 for line in result.log if line.startswith("warning: dropped")) == len(changes)
    # control: against a policy that does not list the Slack user the same change is made, so
    # the guard (and not the plan, or the mocks) is what kept the account safe
    loose_mocks = make_mocks(world_dir)
    control = hand_plan(loose_mocks, world_dir, policy, [change("slack", "suspend", slack_user)], 1)
    loose_writes = write_calls(loose_mocks)
    done, _ = run_hand(control, world_dir, policy, loose_mocks, approvals=1)
    assert [c["account"] for c in done.applied] == [slack_user] and loose_writes


def _login(mocks, user_id):
    return next((u["profile"]["login"] for u in mocks.okta.data["users"] if u["id"] == user_id), "")


def test_the_guard_changes_neighbours_but_not_the_break_glass_account(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    victim = next(u for u in mocks.google.data["users"] if "breakglass" not in u["primaryEmail"])
    changes = [
        change("google", "suspend", BG),
        change("google", "suspend", victim["primaryEmail"]),
    ]
    p = hand_plan(mocks, world_dir, policy, changes)
    result, _ = run_hand(p, world_dir, policy, mocks, approvals=1)
    assert [c["account"] for c in result.dropped_break_glass] == [BG]
    assert [c["account"] for c in result.applied] == [victim["primaryEmail"]]
    states = {u["primaryEmail"]: u["suspended"] for u in mocks.google.data["users"]}
    assert states[victim["primaryEmail"]] is True
    assert states[BG] is False


# ---------------------------------------------------------------- semantics


def test_the_real_plan_has_no_break_glass_in_what_apply_drops(plan, world_dir, policy):
    result, _ = run(plan, world_dir, policy)
    assert result.dropped_break_glass == []  # the diff already kept them out


def test_suspend_comes_before_remove_and_each_action_has_its_api_meaning(
    everything, world_dir, policy
):
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    seen = []
    for mock in (mocks.okta, mocks.google, mocks.slack):
        original = mock.request

        def spy(method, path, *, _o=original, **kw):
            if method != "GET":
                seen.append((method, path))
            return _o(method, path, **kw)

        mock.request = spy
    result, _ = run_hand(everything, world_dir, policy, mocks)
    order = [apply_mod._step(c) for c in result.applied]
    assert order == sorted(order)
    assert order[0] == 0 and order[-1] == 3
    first_end = next(i for i, (m, p) in enumerate(seen) if "deactivate" in p)
    assert not any("/lifecycle/suspend" in p for _, p in seen[first_end:])
    assert not any(m == "DELETE" for m, _ in seen[first_end:])
    after = snapshot(mocks)
    by_login = {u["profile"]["login"]: u["status"] for u in after["okta"]["users"]}
    was_login = {u["profile"]["login"]: u["status"] for u in before["okta"]["users"]}
    google = {u["primaryEmail"]: u["suspended"] for u in after["google"]["users"]}
    slack = {u["userName"]: u["active"] for u in after["slack"]["Resources"]}
    for c in result.applied:
        a = c["account"]
        if c["system"] == "okta" and not c["app_id"]:
            assert by_login[a] == ("SUSPENDED" if c["action"] == "suspend" else "DEPROVISIONED")
        elif c["system"] == "google":
            assert google[a] is True  # remove is a suspension: delete in a later plan
        elif c["system"] == "slack":
            assert slack[a] is False
    assert any(was_login[c["account"]] == "ACTIVE" for c in result.applied if c["system"] == "okta")
    google_removes = [
        c for c in result.applied if c["system"] == "google" and c["action"] == "remove"
    ]
    assert google_removes
    assert [c["account"] for c in result.deferred_deletes] == [c["account"] for c in google_removes]
    assert any("delete in a later plan" in line for line in result.log)
    unassigned = [
        c
        for c in result.applied
        if c["system"] == "okta" and c["app_id"] and c["action"] == "remove"
    ]
    assigned = [c for c in result.applied if c["action"] == "add"]
    assert unassigned and assigned
    assert len(after["okta"]["assignments"]) == (
        len(before["okta"]["assignments"]) - len(unassigned) + len(assigned)
    )


def test_applying_the_same_plan_twice_changes_nothing_the_second_time(plan, world_dir, policy):
    first, mocks = run(plan, world_dir, policy)
    assert first.applied
    mid = snapshot(mocks)
    again = replanned(plan, mocks, world_dir, policy)
    assert again.data["actual_state_sha256"] != plan.data["actual_state_sha256"]
    # the change-by-change check: the same changes against the new state, handed to apply as
    # what a rebuild returns. (A real rebuild would be empty, see the next test.)
    second, _ = run(again, world_dir, policy, mocks, rebuild=lambda state: again)
    assert second.applied == [] and not second.refused_reason
    assert len(second.skipped_already_done) == len(first.applied)
    assert snapshot(mocks) == mid


def test_the_old_plan_is_stale_after_the_first_run(plan, world_dir, policy):
    _, mocks = run(plan, world_dir, policy)
    again, _ = run(plan, world_dir, policy, mocks)
    assert again.refused_reason == "stale"


def test_a_re_plan_after_apply_has_nothing_left_to_apply(everything, world_dir, policy):
    _, mocks = run_hand(everything, world_dir, policy, make_mocks(world_dir))
    state = actual_state(mocks, policy, world_dir / "exports")
    fresh = build_plan(world_dir, policy, state=state)
    removals = [
        c
        for c in fresh.changes
        if c["action"] in ("remove", "suspend")
        and c["system"] in ("okta", "google", "slack")
        and not (c["system"] == "google" and c["action"] == "remove")
    ]
    assert removals == []
    fresh_main, _ = split_plan(fresh, policy)
    result, _ = run(fresh_main, world_dir, policy, mocks, approvals=1)
    assert result.applied == [] and not result.refused_reason


def test_retries_after_429_and_5xx_give_the_same_final_state(plan, world_dir, policy):
    clean, clean_mocks = run(plan, world_dir, policy)
    want = snapshot(clean_mocks)
    noisy_mocks = make_mocks(world_dir)
    for mock in (noisy_mocks.okta, noisy_mocks.google, noisy_mocks.slack):
        mock.fault_rate = 0.2
    sleeps = []
    tokens = issue_write_tokens(noisy_mocks)
    result = apply_plan(
        plan,
        noisy_mocks,
        mode="mock-live",
        approvals=2,
        policy=policy,
        write_token=tokens,
        sleep=sleeps.append,
        world_dir=world_dir,
    )
    assert not result.refused_reason and not result.failed
    assert sleeps, "the injected faults should have caused retries"
    assert noisy_mocks.okta.request_count > clean_mocks.okta.request_count
    assert snapshot(noisy_mocks) == want
    assert len(result.applied) == len(clean.applied)
    # and again, under faults: nothing more to do
    again = replanned(plan, noisy_mocks, world_dir, policy)
    second = apply_plan(
        again,
        noisy_mocks,
        mode="mock-live",
        approvals=2,
        policy=policy,
        write_token=tokens,
        sleep=lambda s: None,
        world_dir=world_dir,
        rebuild=lambda state: again,
    )
    assert second.applied == [] and snapshot(noisy_mocks) == want


# ---------------------------------------------------------------- non-SSO and drift


def test_export_flags_become_manual_tasks_and_not_api_calls(plan, world_dir, policy):
    result, _mocks = run(plan, world_dir, policy)
    removals = [t for t in result.manual_tasks if t.get("action") != "add"]
    adds = [t for t in result.manual_tasks if t.get("action") == "add"]
    assert removals == plan.data["manual_tasks"] and len(removals) > 0
    export_adds = [
        c
        for c in plan.changes
        if c["action"] == "add" and c["system"] not in ("okta", "google", "slack")
    ]
    assert adds and len(adds) == len(export_adds)
    assert all(t["task"].startswith("add ") and " to " in t["task"] for t in adds)
    export_flags = [
        c
        for c in plan.changes
        if c["action"] == "flag" and c["system"] not in ("okta", "google", "slack", "hris")
    ]
    assert len(export_flags) == len(removals)
    assert not any(c["system"] == c["app_id"] for c in result.applied)


def test_drift_flags_are_listed_for_review_and_change_nothing(everything, world_dir, policy):
    result, _ = run_hand(everything, world_dir, policy, make_mocks(world_dir))
    flags = [c for c in everything.changes if c["type"] == "drift" and c["action"] == "flag"]
    assert flags
    assert [c["account"] for c in result.flagged_for_review] == [c["account"] for c in flags]
    assert not any(c["action"] == "flag" for c in result.applied)


def without_reason(task):
    return {k: v for k, v in task.items() if k != "reason"}


def _csv_row_lines(path: Path, needle: str) -> list[int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    return [i for i, ln in enumerate(lines) if needle in ln]


def test_manual_tasks_are_done_when_the_row_is_gone_from_the_next_export(
    plan, world_dir, policy, tmp_path
):
    tasks = plan.data["manual_tasks"]
    nxt = tmp_path / "next"
    shutil.copytree(world_dir / "exports", nxt)
    removable = []
    for t in tasks:
        path = nxt / f"{t['app_id']}.csv"
        if path.is_file() and len(_csv_row_lines(path, t["account"])) == 1:
            removable.append(t)
    assert len(removable) >= 2
    gone, kept = removable[0], removable[1]
    path = nxt / f"{gone['app_id']}.csv"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    del lines[_csv_row_lines(path, gone["account"])[0]]
    path.write_text("".join(lines), encoding="utf-8")
    done, pending = confirm_manual_tasks(tasks, nxt, policy)
    assert gone in done and kept in [without_reason(t) for t in pending]
    assert len(done) + len(pending) == len(tasks)
    assert all(t["reason"] for t in pending)
    # nothing changed: everything is still pending
    done_none, pending_none = confirm_manual_tasks(tasks, world_dir / "exports", policy)
    assert done_none == [] and [without_reason(t) for t in pending_none] == tasks


def test_a_task_whose_export_file_is_missing_is_not_done(plan, world_dir, policy, tmp_path):
    tasks = plan.data["manual_tasks"]
    nxt = tmp_path / "next"
    shutil.copytree(world_dir / "exports", nxt)
    victim = tasks[0]["app_id"]
    (nxt / f"{victim}.csv").unlink()
    done, pending = confirm_manual_tasks(tasks, nxt, policy)
    assert all(t["app_id"] != victim for t in done)
    assert [t for t in tasks if t["app_id"] == victim] == [
        without_reason(t) for t in pending if t["app_id"] == victim
    ]


# ---------------------------------------------------------------- drift in the plan files


def test_drift_is_in_plan_md_and_the_slack_message_with_the_plan_json_count(parts, combined):
    """The real tuning parts: drift in main plus drift in the held plan is the combined drift."""
    main, held = parts
    in_main = main.counts["by_type"].get("drift", 0)
    in_held = main.data["held_plan"]["by_type"].get("drift", 0)
    combined_drift = combined.counts["by_type"]["drift"]
    assert combined_drift > 0 and in_main + in_held == combined_drift
    assert in_held == held.counts["by_type"].get("drift", 0)
    title = "Drift (changed outside the process)"
    md = main.markdown()
    assert f"| {title} | {in_main} |" in md  # main's own table
    if in_held:
        held_section = md.split("## Held plan (needs two approvers)\n", 1)[1].split("\n## ", 1)[0]
        assert f"- {title}: {in_held}" in held_section
    message = slack_mock.build_message(main, "https://example.com/pr")
    warnings = next(
        b["text"]["text"]
        for b in message["blocks"]
        if b.get("type") == "section" and b["text"]["text"].startswith("*Warnings*")
    )
    held_line = next(ln for ln in warnings.splitlines() if ln.startswith("• Held plan"))
    if in_held:
        assert f"{title}: {in_held}" in held_line
    if in_main:
        assert re.search(rf"Drift \(changed outside the process\): {in_main}\b", warnings)


# ---------------------------------------------------------------- CLI


def test_the_cli_is_a_dry_run_unless_the_env_says_mock_live(
    parts, world_dir, policy, tmp_path, monkeypatch, capsys
):
    plan, held = parts
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.json(), encoding="utf-8")
    argv = ["--plan", str(plan_file), "--world", str(world_dir), "--approvals", "1"]
    monkeypatch.delenv("ACCESS_APPLY_MODE", raising=False)
    assert apply_mod.main([*argv, "--log", str(tmp_path / "log.json")]) == 0
    out = capsys.readouterr().out
    assert "mode: dry-run" in out and f"applied: {len(api_changes(plan))}" in out
    assert json.loads((tmp_path / "log.json").read_text())["mode"] == "dry-run"
    monkeypatch.setenv("ACCESS_APPLY_MODE", "production")
    assert apply_mod.main(argv) == 0 and "mode: dry-run" in capsys.readouterr().out
    monkeypatch.setenv("ACCESS_APPLY_MODE", "mock-live")
    assert apply_mod.main(argv) == 0 and "mode: mock-live" in capsys.readouterr().out
    assert apply_mod.main([*argv[:-1], "1"]) == 0  # the main part needs one approval
    capsys.readouterr()
    held_file = tmp_path / "held.json"
    held_file.write_text(held.json(), encoding="utf-8")
    # the CLI never applies the held part, even with a self-declared second approver
    for n in ("1", "2"):
        held_argv = ["--plan", str(held_file), "--world", str(world_dir), "--approvals", n]
        assert apply_mod.main(held_argv) == 1
        out = capsys.readouterr().out
        assert "the held plan is applied only when its pull request is merged with two" in out
        assert "owner's GitHub App and is not built" in out
        assert "applied:" not in out


# ---------------------------------------------------------------- approved content is applied


def test_a_plan_claiming_one_approval_with_fifty_removals_needs_two(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    removals = [c for c in plan.changes if c["action"] in ("remove", "suspend")][:50]
    # two people, no cause dates: neither people leg can fire, only the backstop can
    rows = [{**c, "employee_id": f"E{i % 2}", "cause_date": ""} for i, c in enumerate(removals)]
    assert len(rows) == policy.settings.blast_radius_removals == 50
    hand = hand_plan(mocks, world_dir, policy, rows, approvals_required=1)
    before = snapshot(mocks)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals" and snapshot(mocks) == before
    ok, _ = run_hand(hand, world_dir, policy, make_mocks(world_dir), approvals=2)
    assert not ok.refused_reason
    below = hand_plan(mocks, world_dir, policy, rows[:49], approvals_required=1)
    one, _ = run_hand(below, world_dir, policy, make_mocks(world_dir), approvals=1)
    assert not one.refused_reason


def test_a_plan_of_another_version_is_refused_before_anything_else(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    old = Plan({**plan.data, "version": 1})
    before = snapshot(mocks)
    result, _ = run(old, world_dir, policy, mocks, approvals=2)
    assert result.refused_reason == "plan version 1, expected 2: build the plan again"
    assert snapshot(mocks) == before
    result, _ = run(old, world_dir, policy, make_mocks(world_dir), approvals=0)
    assert "plan version 1" in result.refused_reason  # before the approvals check


def _tampered(plan, index, **fields):
    data = copy.deepcopy(plan.data)
    data["changes"][index].update(fields)
    return Plan(data)


def test_editing_a_cause_date_or_owner_in_the_plan_file_is_refused_as_changed(
    plan, world_dir, policy
):
    index = next(
        i
        for i, c in enumerate(plan.changes)
        if c["action"] in ("remove", "suspend") and c["cause_date"] and c["employee_id"]
    )
    for edit in ({"cause_date": "2026-11-02"}, {"employee_id": "E-tampered"}):
        edited = _tampered(plan, index, **edit)
        assert edited.changes[index] != plan.changes[index]
        mocks = make_mocks(world_dir)
        before = snapshot(mocks)
        result, _ = run(edited, world_dir, policy, mocks, approvals=2)
        assert result.refused_reason == "plan changed", edit
        assert snapshot(mocks) == before


def test_a_plan_claiming_one_approval_with_five_people_on_one_day_needs_two(world_dir, policy):
    mocks = make_mocks(world_dir)
    rows = []
    for i in range(5):
        row = change("okta", "suspend", f"p{i}@meridian.example")
        rows.append({**row, "employee_id": f"E{i}", "cause_date": "2026-11-10"})
    hand = hand_plan(mocks, world_dir, policy, rows, approvals_required=1)
    before = snapshot(mocks)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals" and snapshot(mocks) == before


def test_a_plan_without_cause_dates_is_refused_with_a_clear_reason(world_dir, policy):
    mocks = make_mocks(world_dir)
    row = {
        k: v
        for k, v in change("okta", "suspend", "x@meridian.example").items()
        if k != "cause_date"
    }
    hand = hand_plan(mocks, world_dir, policy, [row], approvals_required=1)
    before = snapshot(mocks)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=2)
    assert "cause_date" in result.refused_reason and snapshot(mocks) == before


@pytest.mark.parametrize("bad", [None, 5, "garbage"])
def test_a_plan_with_a_bad_cause_date_is_refused_with_a_clear_reason(world_dir, policy, bad):
    mocks = make_mocks(world_dir)
    row = {**change("okta", "suspend", "x@meridian.example"), "cause_date": bad}
    hand = hand_plan(mocks, world_dir, policy, [row], approvals_required=1)
    before = snapshot(mocks)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=2)
    assert "cause_date" in result.refused_reason and snapshot(mocks) == before


def test_a_plan_with_a_block_claiming_one_approval_needs_two(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    block = [change("hris", "block_two_approvers", "", type_="hris_mass_termination")]
    hand = hand_plan(mocks, world_dir, policy, block, approvals_required=1)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals"


# ---------------------------------------------------------------- contradictory plans (AAC-017)


def all_calls(mocks):
    """Every request made to each mock from now on, reads included."""
    seen = []
    for mock in (mocks.okta, mocks.google, mocks.slack):
        original = mock.request

        def spy(method, path, *, _o=original, **kw):
            seen.append((method, path))
            return _o(method, path, **kw)

        mock.request = spy
    return seen


def _mover_add(plan):
    return next(c for c in plan.changes if c["system"] == "okta" and c["action"] == "add")


def test_a_plan_that_adds_and_removes_one_target_is_refused_before_anything_is_read(
    plan, world_dir, policy
):
    add = _mover_add(plan)
    undo = {**add, "action": "remove", "account": f"  {add['account'].upper()} "}
    rows = [*plan.changes, undo]
    target = f"{add['account'].casefold()} (okta/{add['app_id']})"
    reason = f"contradictory plan: it adds and removes {target}"
    for mode, approvals in (("mock-live", 2), ("dry-run", 2), ("mock-live", 0)):
        mocks = make_mocks(world_dir)
        hand = hand_plan(mocks, world_dir, policy, rows)
        before = snapshot(mocks)
        calls = all_calls(mocks)
        result, _ = run_hand(hand, world_dir, policy, mocks, mode=mode, approvals=approvals)
        assert result.refused_reason == reason, (mode, approvals)
        assert result.log[-1] == f"refused: {reason}"
        assert calls == [] and snapshot(mocks) == before
        assert result.applied == result.skipped_already_done == result.failed == []
        assert result.dropped_break_glass == result.manual_tasks == []
    # the control: the same changes without the removal are applied
    mocks = make_mocks(world_dir)
    ok, _ = run_hand(hand_plan(mocks, world_dir, policy, plan.changes), world_dir, policy, mocks)
    assert not ok.refused_reason and add["account"] in {c["account"] for c in ok.applied}


def test_the_cli_refuses_a_contradictory_plan_with_exit_code_1(
    plan, world_dir, tmp_path, monkeypatch, capsys
):
    add = _mover_add(plan)
    data = {**plan.data, "changes": [*plan.changes, {**add, "action": "suspend"}]}
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(Plan(data).json(), encoding="utf-8")
    log = tmp_path / "log.json"
    monkeypatch.setenv("ACCESS_APPLY_MODE", "mock-live")
    argv = ["--plan", str(plan_file), "--world", str(world_dir), "--approvals", "1"]
    assert apply_mod.main([*argv, "--log", str(log)]) == 1
    out = capsys.readouterr().out
    target = f"{add['account'].casefold()} (okta/{add['app_id']})"
    assert f"refused: contradictory plan: it adds and removes {target}" in out
    assert "applied: 0  already done: 0" in out
    assert json.loads(log.read_text())["applied"] == []


A = "ana@meridian.example"


@pytest.mark.parametrize(
    ("rows", "clashes"),
    [
        # a removal, a suspension, and the flag on an export row each take access away
        (
            [change("okta", "add", A, "amber-board"), change("okta", "remove", A, "amber-board")],
            [f"{A} (okta/amber-board)"],
        ),
        (
            [
                change("okta", "suspend", f"  {A.upper()} ", "amber-board"),
                change("okta", "add", A, "amber-board"),
            ],
            [f"{A} (okta/amber-board)"],
        ),
        ([change("okta", "add", A), change("okta", "suspend", A)], [f"{A} (okta)"]),
        ([change("google", "remove", A), change("google", "add", A.title())], [f"{A} (google)"]),
        ([change("slack", "add", "Ana"), change("slack", "remove", "ana")], ["ana (slack)"]),
        (
            [
                change("birch-notes", "flag", "Ana", "birch-notes"),
                change("birch-notes", "add", "ana", "birch-notes"),
            ],
            ["ana (birch-notes/birch-notes)"],
        ),
        (  # every target is named, sorted by system, app and account
            [
                change("slack", "remove", "ana"),
                change("okta", "remove", "cara@meridian.example", "amber-board"),
                change("slack", "add", "ana"),
                change("okta", "add", "cara@meridian.example", "amber-board"),
                change("okta", "add", "ben@meridian.example", "amber-board"),
                change("okta", "remove", "ben@meridian.example", "amber-board"),
            ],
            [
                "ben@meridian.example (okta/amber-board)",
                "cara@meridian.example (okta/amber-board)",
                "ana (slack)",
            ],
        ),
        # not one target, or not taking access away
        ([change("okta", "add", A, "amber-board"), change("okta", "remove", A, "amber-desk")], []),
        ([change("okta", "add", A, "amber-board"), change("okta", "suspend", A)], []),
        ([change("okta", "add", A, "amber-board"), change("slack", "remove", "ana")], []),
        (
            [
                change("okta", "remove", "ana", "amber-board"),
                change("okta", "add", A, "amber-board"),
            ],
            [],
        ),
        ([change("okta", "add", A, "amber-board"), change("okta", "flag", A, "amber-board")], []),
        ([change("hris", "add", "E0"), change("hris", "flag", "E0")], []),
        ([change("okta", "add", A), change("okta", "never_touch", A)], []),
        ([change("okta", "add", A, "amber-board"), change("okta", "add", A, "amber-board")], []),
        ([change("slack", "remove", "ana"), change("slack", "suspend", "ana")], []),
        ([], []),
    ],
)
def test_what_counts_as_adding_and_taking_away_one_target(rows, clashes):
    assert contradictions(rows) == clashes
    assert contradictions(reversed(rows)) == clashes


def test_the_diff_never_asks_to_add_and_take_away_one_target(combined, parts):
    """A sanity check on the tuning world: its plans have adds, removals and export flags, and
    no target gets both, so the refusal never stops a plan the diff built."""
    bulk = [c for c in combined.data["held"] if c.get("hold") == "bulk"]
    assert {"add", "remove", "suspend", "flag"} <= {c["action"] for c in combined.changes}
    for rows in ([*combined.changes, *bulk], parts[0].changes, parts[1].changes):
        assert contradictions(rows) == []


def test_hr_reversing_a_termination_after_the_plan_refuses_it(plan, world_dir, policy, tmp_path):
    dst = tmp_path / "world"
    shutil.copytree(world_dir, dst)
    hris = json.loads((dst / "hris.json").read_text(encoding="utf-8"))
    people = {c["employee_id"] for c in plan.changes if c["type"] == "leaver_access_left"}
    event = next(
        e
        for e in hris["events"]
        if e["kind"] == "terminate" and e["source"] == "hr" and e["employee_id"] in people
    )
    hris["events"].remove(event)
    for record in hris["records"]:
        if record["employee_id"] == event["employee_id"]:
            record["status"], record["end_date"] = "active", ""
    (dst / "hris.json").write_text(json.dumps(hris), encoding="utf-8")
    mocks = make_mocks(world_dir)  # the systems are exactly as planned: it is not "stale"
    before = snapshot(mocks)
    result, _ = run(plan, dst, policy, mocks)
    assert result.refused_reason == "plan changed"
    assert not result.applied and snapshot(mocks) == before
    assert "changes" in " ".join(result.log)


def test_a_changed_policy_refuses_the_plan(plan, world_dir, policy, tmp_path):
    pdir = tmp_path / "policy"
    shutil.copytree(default_policy_dir(), pdir)
    settings = pdir / "settings.yaml"
    text = settings.read_text(encoding="utf-8")
    assert "blast_radius_removals: 50" in text
    settings.write_text(
        text.replace("blast_radius_removals: 50", "blast_radius_removals: 60"), encoding="utf-8"
    )
    changed = load_policy(pdir)
    assert changed.settings.blast_radius_removals == 60
    mocks = make_mocks(world_dir)
    before = snapshot(mocks)
    result, _ = run(plan, world_dir, changed, mocks, policy_dir=pdir)
    assert result.refused_reason == "plan changed" and "policy_sha256" in " ".join(result.log)
    assert snapshot(mocks) == before


def test_apply_needs_a_world_to_rebuild_the_plan_from(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=2,
        policy=policy,
        write_token=issue_write_tokens(mocks),
        exports_dir=world_dir / "exports",
    )
    assert result.refused_reason == "no world to rebuild the plan from"


# ---------------------------------------------------------------- 404s, lost answers, verification


def test_a_404_from_a_wrong_route_is_a_failure_not_already_done(
    plan, world_dir, policy, monkeypatch
):
    original = apply_mod._plan_call

    def wrong(c, a):
        call = original(c, a)
        if call and call[0] == "okta" and "/lifecycle/" in call[2]:
            return (call[0], call[1], call[2].replace("/lifecycle/", "/lifecycl/"), *call[3:])
        if call and call[0] == "slack":
            return (call[0], call[1], call[2].replace("/Users/", "/Userz/"), *call[3:])
        return call

    monkeypatch.setattr(apply_mod, "_plan_call", wrong)
    mocks = make_mocks(world_dir)
    result, _ = run(plan, world_dir, policy, mocks)
    failed = {(c["system"], c["app_id"] == "") for c in result.failed}
    assert ("okta", True) in failed and ("slack", True) in failed
    assert all(c["error"] == "status 404" for c in result.failed)
    assert not any(
        c["system"] == "slack" or (c["system"] == "okta" and not c["app_id"])
        for c in result.skipped_already_done
    )
    assert not any(c["system"] == "slack" for c in result.applied)


def test_an_unknown_okta_user_is_a_failure_and_a_gone_assignment_is_already_done(
    plan, world_dir, policy
):
    mocks = make_mocks(world_dir)
    suspend = next(c for c in plan.changes if c["system"] == "okta" and c["action"] == "suspend")
    unassign = next(
        c for c in plan.changes if c["system"] == "okta" and c["app_id"] and c["action"] == "remove"
    )
    hand = hand_plan(mocks, world_dir, policy, [suspend, unassign])
    original = mocks.okta.request

    def vanish(method, path, **kw):
        if method != "GET":  # the user and the assignment disappear just before the write
            mocks.okta.data["users"] = [
                u for u in mocks.okta.data["users"] if u["profile"]["login"] != suspend["account"]
            ]
            mocks.okta.data["assignments"] = []
        return original(method, path, **kw)

    mocks.okta.request = vanish
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert [c["action"] for c in result.failed] == ["suspend"]
    assert [c["action"] for c in result.skipped_already_done] == ["remove"]


def test_an_applied_change_that_did_not_take_effect_is_reported_as_failed(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    original = mocks.google.request

    def swallow(method, path, **kw):
        if method == "PATCH":
            return MockResponse(200, {}, {})  # says yes, changes nothing
        return original(method, path, **kw)

    mocks.google.request = swallow
    result, _ = run(plan, world_dir, policy, mocks)
    google = [c for c in plan.changes if c["system"] == "google" and c["action"] != "never_touch"]
    assert google
    assert [c["system"] for c in result.failed] == ["google"] * len(google)
    assert all("not in the wanted state" in c["error"] for c in result.failed)
    assert not any(c["system"] == "google" for c in result.applied)
    assert result.applied  # the others took effect


def lose_one_answer(mock, wanted):
    """The first write that `wanted(method, path)` accepts goes through, and the answer is a 503."""
    original = mock.request
    state = {"lost": 0}

    def wrapper(method, path, **kw):
        response = original(method, path, **kw)
        if method != "GET" and not state["lost"] and wanted(method, path):
            state["lost"] = 1
            return MockResponse(503, {}, {"error": "answer lost"})
        return response

    mock.request = wrapper
    return state


def test_a_lost_acknowledgement_is_retried_safely_and_the_counts_add_up(
    everything, world_dir, policy
):
    plan = everything
    clean, clean_mocks = run_hand(plan, world_dir, policy, make_mocks(world_dir))
    want = snapshot(clean_mocks)
    mocks = make_mocks(world_dir)
    lost = [
        lose_one_answer(mocks.okta, lambda m, p: m == "DELETE"),
        lose_one_answer(mocks.okta, lambda m, p: p.endswith("/lifecycle/suspend")),
        lose_one_answer(mocks.okta, lambda m, p: p.endswith("/lifecycle/deactivate")),
        lose_one_answer(mocks.okta, lambda m, p: m == "POST" and p.startswith("/api/v1/apps/")),
        lose_one_answer(mocks.google, lambda m, p: m == "PATCH"),
        lose_one_answer(mocks.slack, lambda m, p: m == "PATCH"),
    ]
    result, _ = run_hand(plan, world_dir, policy, mocks)
    assert all(s["lost"] for s in lost), "every kind of write should have lost one answer"
    assert not result.refused_reason and not result.failed
    assert snapshot(mocks) == want
    assert len(result.applied) + len(result.skipped_already_done) == len(clean.applied)
    # a retried unassignment finds the assignment gone: that, and only that, is "already done"
    assert [c["action"] for c in result.skipped_already_done] == ["remove"]
    assert all(c["app_id"] and c["system"] == "okta" for c in result.skipped_already_done)


# ---------------------------------------------------------------- Okta assignment (mover adds)


def test_the_okta_mock_assigns_a_user_to_an_app_idempotently(world_dir):
    mocks = make_mocks(world_dir)
    write = mocks.okta.issue_token("w", {"read", "write"})
    read = mocks.okta.issue_token("r", {"read"})
    app = mocks.okta.data["apps"][0]["id"]
    taken = {a["userId"] for a in mocks.okta.data["assignments"] if a["appId"] == app}
    user = next(u["id"] for u in mocks.okta.data["users"] if u["id"] not in taken)
    path = f"/api/v1/apps/{app}/users"
    body = {"id": user, "scope": "USER", "profile": {"role": "member"}}
    assert mocks.okta.request("POST", path, token=read, body=body).status == 403
    assert mocks.okta.request("POST", path, token=write, body=body).status == 200
    count = len(mocks.okta.data["assignments"])
    assert mocks.okta.request("POST", path, token=write, body=body).status == 200
    assert len(mocks.okta.data["assignments"]) == count
    mine = [a for a in mocks.okta.data["assignments"] if a["appId"] == app and a["userId"] == user]
    assert len(mine) == 1 and mine[0]["profile"]["role"] == "member" and mine[0]["scope"] == "USER"
    assert (
        mocks.okta.request("POST", "/api/v1/apps/nope/users", token=write, body=body).status == 404
    )
    ghost = {**body, "id": "00u-nobody"}
    assert mocks.okta.request("POST", path, token=write, body=ghost).status == 404


def test_mover_adds_for_sso_apps_assign_through_okta_and_are_idempotent(plan, world_dir, policy):
    adds = [c for c in plan.changes if c["system"] == "okta" and c["action"] == "add"]
    assert adds
    result, mocks = run(plan, world_dir, policy)
    assert {c["account"] for c in result.applied if c["action"] == "add"} == {
        c["account"] for c in adds
    }
    app_id = {a["name"]: a["id"] for a in mocks.okta.data["apps"]}
    user_id = {u["profile"]["login"]: u["id"] for u in mocks.okta.data["users"]}
    have = {(a["appId"], a["userId"]) for a in mocks.okta.data["assignments"]}
    for c in adds:
        assert (app_id[c["app_id"]], user_id[c["account"]]) in have
    assert all(
        c["call"].startswith("POST /api/v1/apps/") for c in result.applied if c["action"] == "add"
    )
    # again, against the new state: already assigned, nothing is sent
    again = replanned(plan, mocks, world_dir, policy)
    writes = write_calls(mocks)
    second, _ = run(again, world_dir, policy, mocks, rebuild=lambda state: again)
    assert second.applied == [] and writes == []
    assert [c["account"] for c in second.skipped_already_done if c["action"] == "add"] == [
        c["account"] for c in adds
    ]


# ---------------------------------------------------------------- manual tasks and the next export


def test_a_truncated_or_unreadable_next_export_confirms_nothing(plan, world_dir, policy, tmp_path):
    tasks = [t for t in plan.data["manual_tasks"] if t.get("action") != "add"]
    nxt = tmp_path / "next"
    shutil.copytree(world_dir / "exports", nxt)
    empty, damaged = (
        tasks[0]["app_id"],
        next(t["app_id"] for t in tasks if t["app_id"] != tasks[0]["app_id"]),
    )
    (nxt / f"{empty}.csv").write_text("", encoding="utf-8")  # truncated to nothing: no header
    bad = nxt / f"{damaged}.csv"
    removed = next(t for t in tasks if t["app_id"] == damaged)
    lines = bad.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    lines = [ln for ln in lines if removed["account"] not in ln] + ["stray,cell\n"]
    bad.write_text("".join(lines), encoding="utf-8")
    done, pending = confirm_manual_tasks(tasks, nxt, policy)
    assert not any(t["app_id"] in (empty, damaged) for t in done)
    reasons = {t["app_id"]: t["reason"] for t in pending if t["app_id"] in (empty, damaged)}
    assert "header" in reasons[empty] and "unreadable" in reasons[damaged]


def test_an_add_task_is_done_when_the_row_appears(plan, world_dir, policy, tmp_path):
    result, _ = run(plan, world_dir, policy)
    adds = [t for t in result.manual_tasks if t.get("action") == "add"]
    assert adds
    done, pending = confirm_manual_tasks(adds, world_dir / "exports", policy)
    assert done == [] and len(pending) == len(adds)  # the rows are not there yet
    nxt = tmp_path / "next"
    shutil.copytree(world_dir / "exports", nxt)
    task = adds[0]
    path = nxt / f"{task['app_id']}.csv"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    first = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip(", "))
    cells = lines[first].split(",")
    cells[0] = task["account"]
    lines.insert(first, ",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    done, _ = confirm_manual_tasks(adds, nxt, policy)
    assert [t["account"] for t in done] == [task["account"]]


# ---------------------------------------------------------------- minor


def test_a_google_remove_on_an_already_suspended_account_still_records_the_later_delete(
    world_dir, policy
):
    mocks = make_mocks(world_dir)
    victim = next(u for u in mocks.google.data["users"] if "breakglass" not in u["primaryEmail"])
    token = mocks.google.issue_token("setup", {"read", "write"})
    mocks.google.request(
        "PATCH",
        f"/admin/directory/v1/users/{victim['id']}",
        token=token,
        body={"suspended": True},
    )
    hand = hand_plan(mocks, world_dir, policy, [change("google", "remove", victim["primaryEmail"])])
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert [c["why"] for c in result.skipped_already_done] == ["already suspended"]
    assert [c["account"] for c in result.deferred_deletes] == [victim["primaryEmail"]]
    assert any("delete in a later plan" in line for line in result.log)


def test_hr_flags_are_not_manual_tasks_anywhere(world_dir, policy):
    mocks = make_mocks(world_dir)
    flag = change("hris", "flag", "E1", type_="hr_data_error")
    hand = hand_plan(mocks, world_dir, policy, [flag])
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.manual_tasks == [] and [c["account"] for c in result.not_applied] == ["E1"]
    assert not any(line.startswith("manual task") for line in result.log)
