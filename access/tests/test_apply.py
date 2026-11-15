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
    issue_write_tokens,
    normalise_mode,
)
from meridian_access.collect import Mocks, actual_state
from meridian_access.mocks import GoogleMock, MockResponse, OktaMock, SlackMock
from meridian_access.plan import Plan, build_plan
from meridian_access.policy.load import default_policy_dir, load_policy

SRC = Path(apply_mod.__file__)
NETWORK = {"urllib", "http", "socket", "requests", "httpx", "slack_sdk", "ssl", "aiohttp"}
BG = "breakglass-01@meridian.example"


@pytest.fixture(scope="module")
def plan(world_dir, policy) -> Plan:
    return build_plan(world_dir, policy)


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
    """Apply a hand-made plan: the rebuild step returns that same plan, because the diff would
    never produce the changes a guard test needs (the guard is what is under test)."""
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
            "version": 1,
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


def test_missing_approvals_are_refused_and_nothing_changes(plan, world_dir, policy):
    assert plan.data["approvals_required"] == 2
    for have in (0, 1):
        mocks = make_mocks(world_dir)
        before = snapshot(mocks)
        result, _ = run(plan, world_dir, policy, mocks, approvals=have)
        assert result.refused_reason == "needs 2 approvals"
        assert not result.applied and snapshot(mocks) == before


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
    assert result.held_not_applied == len(plan.data["held"])
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
    changes = []
    for action in ("suspend", "remove"):
        for system, accounts in names.items():
            changes += [change(system, action, account) for account in accounts]
    changes += [change("okta", "remove", account, app_id) for account in names["okta"]]
    changes += [change("okta", "add", account, app_id) for account in names["okta"]]
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


def test_suspend_comes_before_remove_and_each_action_has_its_api_meaning(plan, world_dir, policy):
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
    result, _ = run(plan, world_dir, policy, mocks)
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


def test_a_re_plan_after_apply_has_nothing_left_to_apply(plan, world_dir, policy):
    _, mocks = run(plan, world_dir, policy)
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
    result, _ = run(fresh, world_dir, policy, mocks, approvals=fresh.data["approvals_required"])
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


def test_drift_flags_are_listed_for_review_and_change_nothing(plan, world_dir, policy):
    result, _ = run(plan, world_dir, policy)
    flags = [c for c in plan.changes if c["type"] == "drift" and c["action"] == "flag"]
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


def test_drift_is_in_plan_md_and_the_slack_warnings_with_the_plan_json_count(plan):
    drift = plan.counts["by_type"]["drift"]
    assert drift > 0 and drift == sum(1 for c in plan.changes if c["type"] == "drift")
    md = plan.markdown()
    assert f"| Drift (changed outside the process) | {drift} |" in md
    section = md.split("## Drift (changed outside the process)\n", 1)[1].split("\n## ", 1)[0]
    assert len([ln for ln in section.splitlines() if ln.startswith("- ")]) == drift
    message = slack_mock.build_message(plan, "https://example.com/pr")
    warnings = next(
        b["text"]["text"]
        for b in message["blocks"]
        if b.get("type") == "section" and b["text"]["text"].startswith("*Warnings*")
    )
    found = re.search(r"Drift \(changed outside the process\): (\d+)", warnings)
    assert found and int(found.group(1)) == drift


# ---------------------------------------------------------------- CLI


def test_the_cli_is_a_dry_run_unless_the_env_says_mock_live(
    plan, world_dir, policy, tmp_path, monkeypatch, capsys
):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.json(), encoding="utf-8")
    argv = ["--plan", str(plan_file), "--world", str(world_dir), "--approvals", "2"]
    monkeypatch.delenv("ACCESS_APPLY_MODE", raising=False)
    assert apply_mod.main([*argv, "--log", str(tmp_path / "log.json")]) == 0
    out = capsys.readouterr().out
    assert "mode: dry-run" in out and f"applied: {len(api_changes(plan))}" in out
    assert json.loads((tmp_path / "log.json").read_text())["mode"] == "dry-run"
    monkeypatch.setenv("ACCESS_APPLY_MODE", "production")
    assert apply_mod.main(argv) == 0 and "mode: dry-run" in capsys.readouterr().out
    monkeypatch.setenv("ACCESS_APPLY_MODE", "mock-live")
    assert apply_mod.main(argv) == 0 and "mode: mock-live" in capsys.readouterr().out
    assert apply_mod.main([*argv[:-1], "1"]) == 1  # refused: needs 2 approvals
    assert "needs 2 approvals" in capsys.readouterr().out


# ---------------------------------------------------------------- approved content is applied


def test_a_plan_claiming_one_approval_with_forty_removals_needs_two(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    forty = [c for c in plan.changes if c["action"] in ("remove", "suspend")][:40]
    assert len(forty) == 40
    hand = hand_plan(mocks, world_dir, policy, forty, approvals_required=1)
    before = snapshot(mocks)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals" and snapshot(mocks) == before
    ok, _ = run_hand(hand, world_dir, policy, make_mocks(world_dir), approvals=2)
    assert not ok.refused_reason


def test_a_plan_with_a_block_claiming_one_approval_needs_two(plan, world_dir, policy):
    mocks = make_mocks(world_dir)
    block = [change("hris", "block_two_approvers", "", type_="hris_mass_termination")]
    hand = hand_plan(mocks, world_dir, policy, block, approvals_required=1)
    result, _ = run_hand(hand, world_dir, policy, mocks, approvals=1)
    assert result.refused_reason == "needs 2 approvals"


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
    assert "blast_radius_n: 25" in text
    settings.write_text(text.replace("blast_radius_n: 25", "blast_radius_n: 30"), encoding="utf-8")
    changed = load_policy(pdir)
    assert changed.settings.blast_radius_n == 30
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


def test_a_lost_acknowledgement_is_retried_safely_and_the_counts_add_up(plan, world_dir, policy):
    clean, clean_mocks = run(plan, world_dir, policy)
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
    result, _ = run(plan, world_dir, policy, mocks)
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
