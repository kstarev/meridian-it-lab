"""The split plan: per-cause-day limits send over-limit days to a held plan (two approvers) and
leave everything else in the main plan (one approver). Offline; the tuning world is the
practice world."""

import copy
import json
from pathlib import Path

import pytest

from meridian_access.approvals import check, required_for
from meridian_access.desired import load_hris
from meridian_access.plan import Plan, build_plan, main, split_plan

HEAD = "a" * 40
OWNER = {"kstarev"}


def row(account, eid="E1", day="", action="remove", type_="leaver_access_left", hold="", **kw):
    return {
        "type": type_,
        "employee_id": eid,
        "account": account,
        "system": "okta",
        "app_id": "",
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": day,
        "hold": hold,
        **kw,
    }


def synthetic(changes, held=()):
    return Plan(
        {
            "version": 2,
            "as_of": "2026-12-01",
            "actual_state_sha256": "a" * 64,
            "desired_state_sha256": "d" * 64,
            "policy_sha256": "p" * 64,
            "changes": list(changes),
            "held": list(held),
            "warnings": ["w"],
            "suggested_map_entries": [],
            "counts": {},
            "approvals_required": 2,
            "manual_tasks": [],
        }
    )


def people_on(day, n, tag=""):
    return [row(f"{tag}{day}-{i}", eid=f"E{tag}{day}{i}", day=day) for i in range(n)]


def test_one_bad_day_goes_to_the_held_plan_and_the_rest_stays_in_main(policy):
    bad = people_on("2026-11-10", 5)
    ok = people_on("2026-11-11", 3)
    main_plan, held = split_plan(synthetic([*bad, *ok]), policy)
    assert held is not None
    assert {c["account"] for c in held.changes} == {c["account"] for c in bad}
    assert {c["account"] for c in main_plan.changes} == {c["account"] for c in ok}  # none lost
    assert main_plan.data["approvals_required"] == policy.settings.approvals_default == 1
    assert held.data["approvals_required"] == policy.settings.approvals_over_n == 2
    assert (main_plan.data["part"], held.data["part"]) == ("main", "held")
    assert main_plan.data["held_plan"] == {
        "buckets": [{"day": "2026-11-10", "people": 5, "removals": 5}],
        "changes": 5,
        "by_type": {"leaver_access_left": 5},
        "manual_tasks": 0,
        "approvals_required": 2,
    }
    assert held.data["held_plan"] is None and held.data["held"] == []
    moved = [c for c in main_plan.data["held"]]
    assert len(moved) == 5
    assert all(c["reason"].endswith("; in the held plan: 2026-11-10 over the limit") for c in moved)
    assert main_plan.counts["total"] == 3 and main_plan.counts["held"] == 5
    assert held.counts["total"] == 5 and held.counts["removals_and_suspensions"] == 5


def test_a_plan_with_no_over_limit_bucket_has_no_held_plan(policy):
    plan = synthetic([*people_on("2026-11-10", 4), *people_on("2026-11-11", 4)])
    main_plan, held = split_plan(plan, policy)
    assert held is None and main_plan.data["held_plan"] is None
    assert main_plan.counts["total"] == 8 and main_plan.data["approvals_required"] == 1


def test_the_no_date_bucket_goes_to_the_held_plan_when_over_and_stays_when_under(policy):
    over = [row(f"u{i}", eid=f"E{i}", day="") for i in range(5)]
    main_plan, held = split_plan(synthetic([*over, *people_on("2026-11-10", 2)]), policy)
    assert len(held.changes) == 5 and len(main_plan.changes) == 2
    assert main_plan.data["held_plan"]["buckets"][0]["day"] == "no-date"
    assert all("no date over the limit" in c["reason"] for c in main_plan.data["held"])
    under = [row(f"u{i}", eid=f"E{i}", day="") for i in range(4)]
    main_plan, held = split_plan(synthetic(under), policy)
    assert held is None and len(main_plan.changes) == 4


def test_break_glass_and_hr_data_errors_stay_in_main(policy):
    keep = [
        row("bg", eid="", day="", action="never_touch", type_="break_glass"),
        row("bad", eid="E9", day="", action="flag", type_="hr_data_error"),
    ]
    over = [row(f"u{i}", eid=f"E{i}", day="") for i in range(5)]
    main_plan, held = split_plan(synthetic([*keep, *over]), policy)
    assert {c["account"] for c in main_plan.changes} == {"bg", "bad"}
    assert {c["account"] for c in held.changes} == {c["account"] for c in over}


def test_bad_data_holds_never_go_to_the_held_plan(policy):
    bad = row("held-person", eid="E7", day="2026-11-10", hold="bad_data")
    plan = synthetic([*people_on("2026-11-10", 5)], held=[bad])
    main_plan, held = split_plan(plan, policy)
    assert [c["account"] for c in main_plan.data["held"] if c["hold"] == "bad_data"] == [
        "held-person"
    ]
    assert "held-person" not in {c["account"] for c in held.changes}
    assert "held-person" not in {c["account"] for c in main_plan.changes}
    # and with only a bad-data hold there is no held plan at all
    only, none = split_plan(synthetic([row("x", day="2026-11-10")], held=[bad]), policy)
    assert none is None and only.counts["held"] == 1


def test_bulk_holds_and_the_block_entry_go_to_the_held_plan(policy):
    block = row(
        "",
        eid="",
        action="block_two_approvers",
        type_="hris_mass_termination",
        day="",
        system="hris",
    )
    bulk = [row(f"b{i}", eid=f"B{i}", day="2026-11-29", hold="bulk") for i in range(3)]
    ordinary = people_on("2026-11-10", 2)
    main_plan, held = split_plan(synthetic([block, *ordinary], held=bulk), policy)
    assert {c["account"] for c in held.changes} == {"", "b0", "b1", "b2"}
    assert all(c["hold"] == "" for c in held.changes)  # applied after two approvals
    assert {c["account"] for c in main_plan.changes} == {c["account"] for c in ordinary}
    assert held.data["approvals_required"] == 2 and main_plan.data["approvals_required"] == 1
    assert {c["account"] for c in main_plan.data["held"]} == {"", "b0", "b1", "b2"}
    assert main_plan.data["held_plan"]["buckets"] == []  # 3 people: held for being a bulk import


def test_a_bulk_day_pulls_the_ordinary_changes_of_that_day_into_the_held_plan(policy):
    bulk = [row(f"b{i}", eid=f"B{i}", day="2026-11-29", hold="bulk") for i in range(5)]
    same_day = [row("same-day", eid="E50", day="2026-11-29")]
    main_plan, held = split_plan(synthetic(same_day, held=bulk), policy)
    assert "same-day" in {c["account"] for c in held.changes}
    assert main_plan.changes == []


def test_the_split_partitions_the_changes_and_is_deterministic(policy):
    bulk = [row(f"b{i}", eid=f"B{i}", day="2026-11-29", hold="bulk") for i in range(5)]
    changes = [*people_on("2026-11-10", 5), *people_on("2026-11-11", 3), *people_on("", 2, "u")]
    plan = synthetic(changes, held=bulk)
    a, b = split_plan(plan, policy)
    a2, b2 = split_plan(copy.deepcopy(plan), policy)
    assert a.json() == a2.json() and b.json() == b2.json()
    ids = sorted(c["account"] for c in [*a.changes, *b.changes])
    assert ids == sorted(c["account"] for c in [*changes, *bulk])
    assert not set(c["account"] for c in a.changes) & set(c["account"] for c in b.changes)
    # the hashes and as_of are the same in both parts
    for key in ("as_of", "actual_state_sha256", "desired_state_sha256", "policy_sha256"):
        assert a.data[key] == b.data[key] == plan.data[key]


# ---------------------------------------------------------------- the practice (tuning) world


@pytest.fixture(scope="module")
def combined(world_dir, policy):
    return build_plan(world_dir, policy)


@pytest.fixture(scope="module")
def parts(combined, policy):
    return split_plan(combined, policy)


def test_on_the_tuning_world_only_the_bulk_day_and_the_no_date_backlog_need_two_approvers(
    parts, world_dir
):
    main_plan, _held = parts
    _, events = load_hris(world_dir / "hris.json")
    bulk_dates = {e.date for e in events if e.kind == "terminate" and e.source == "bulk_import"}
    days = [b["day"] for b in main_plan.data["held_plan"]["buckets"]]
    assert sorted(days) == sorted([*bulk_dates, "no-date"])
    ordinary = [d for d in days if d != "no-date" and d not in bulk_dates]
    assert ordinary == []  # no ordinary day needs two approvers


def test_everything_caused_on_the_bulk_day_is_in_the_held_plan_and_none_in_main(
    parts, combined, world_dir
):
    main_plan, held = parts
    _, events = load_hris(world_dir / "hris.json")
    bulk_dates = {e.date for e in events if e.kind == "terminate" and e.source == "bulk_import"}
    on_bulk_day = [c for c in combined.changes if c["cause_date"] in bulk_dates]
    bulk_held = [c for c in combined.data["held"] if c["hold"] == "bulk"]
    assert on_bulk_day or bulk_held
    held_keys = {json.dumps({**c, "hold": "", "reason": ""}, sort_keys=True) for c in held.changes}
    for c in [*on_bulk_day, *bulk_held]:
        assert json.dumps({**c, "hold": "", "reason": ""}, sort_keys=True) in held_keys
    assert not [c for c in main_plan.changes if c["cause_date"] in bulk_dates]
    assert not [c for c in main_plan.changes if c["action"] == "block_two_approvers"]
    assert any(c["action"] == "block_two_approvers" for c in held.changes)
    assert not [
        c for c in main_plan.changes if c["cause_date"] == "" and c["type"] != "break_glass"
    ]
    bad = [c for c in combined.data["held"] if c["hold"] == "bad_data"]
    assert not any(c["hold"] == "bad_data" for c in held.changes)
    assert len([c for c in main_plan.data["held"] if c["hold"] == "bad_data"]) == len(bad)


def test_main_needs_one_and_held_needs_two_on_the_tuning_world(parts, combined, policy):
    main_plan, held = parts
    assert main_plan.data["approvals_required"] == 1 == required_for(main_plan.changes, policy)
    assert held.data["approvals_required"] == 2 == required_for(held.changes, policy)
    assert combined.data["approvals_required"] == 2  # the combined plan is unchanged
    assert "part" not in combined.data and main_plan.changes
    assert main_plan.counts["total"] + held.counts["total"] == combined.counts["total"] + len(
        [c for c in combined.data["held"] if c["hold"] == "bulk"]
    )


def test_a_plan_pull_request_check_needs_two_for_held_and_one_for_main(parts, policy):
    main_plan, held = parts

    def approvals(n):
        return [
            {
                "user": {"login": f"kstarev{i}" if i else "kstarev"},
                "state": "APPROVED",
                "commit_id": HEAD,
                "submitted_at": f"2026-11-15T10:0{i}:00Z",
            }
            for i in range(n)
        ]

    owners = {"kstarev", "kstarev1"}
    ok, _ = check(
        main_plan, approvals(1), owners, HEAD, policy=policy, plan_path="access/plan/plan.json"
    )
    assert ok
    bad, message = check(
        held, approvals(1), owners, HEAD, policy=policy, plan_path="access/plan/held/plan.json"
    )
    assert not bad and "needs 2 approvals" in message
    two, _ = check(
        held, approvals(2), owners, HEAD, policy=policy, plan_path="access/plan/held/plan.json"
    )
    assert two


# ---------------------------------------------------------------- files


def test_the_cli_writes_the_held_plan_to_a_held_folder_and_removes_a_stale_one(
    world_dir, tmp_path, capsys
):
    out = tmp_path / "plan"
    assert main(["--world", str(world_dir), "--out", str(out)]) == 0
    for name in ("plan.json", "PLAN.md", "summary.md"):
        assert (out / "held" / name).is_file(), name
    main_data = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    held_data = json.loads((out / "held" / "plan.json").read_text(encoding="utf-8"))
    assert main_data["part"] == "main" and held_data["part"] == "held"
    held_md = (out / "held" / "PLAN.md").read_text(encoding="utf-8")
    assert held_md.startswith("# Held access plan: needs two approvers")
    assert "no date" in held_md
    main_md = (out / "PLAN.md").read_text(encoding="utf-8")
    assert "## Held plan (needs two approvers)" in main_md and "separate pull request" in main_md
    slack = (out / "slack-message.json").read_text(encoding="utf-8")
    assert "Held plan (its own pull request)" in slack and "needs two approvers" in slack
    printed = capsys.readouterr().out
    assert "main plan:" in printed and "held plan:" in printed
    first = (out / "held" / "plan.json").read_bytes()
    main(["--world", str(world_dir), "--out", str(out)])
    assert (out / "held" / "plan.json").read_bytes() == first

    # a stale held folder is removed when there is no held plan
    from meridian_access import plan as plan_mod

    real = plan_mod.split_plan
    plan_mod.split_plan = lambda p, pol: (real(p, pol)[0], None)
    try:
        main(["--world", str(world_dir), "--out", str(out)])
    finally:
        plan_mod.split_plan = real
    assert not (out / "held").exists()
    assert not [p for p in out.iterdir() if p.name.startswith(".plan-")]


# ---------------------------------------------------------------- what main shows of the held plan


def test_main_plan_md_shows_buckets_and_counts_not_the_moved_changes(policy):
    bad = [
        row(f"mover{i}", eid=f"E{i}", day="2026-11-10", type_="mover_stale_access")
        for i in range(5)
    ]
    drift = row("d1", eid="E9", day="2026-11-10", type_="drift", action="flag")
    keep = row("keeper", eid="K1", day="2026-11-11")
    hold = row("badrow", eid="H1", day="2026-11-12", hold="bad_data")
    main_plan, held = split_plan(synthetic([*bad, drift, keep], held=[hold]), policy)
    assert held is not None
    held_plan = main_plan.data["held_plan"]
    assert held_plan["by_type"] == {"drift": 1, "mover_stale_access": 5}
    assert held_plan["manual_tasks"] == 0 and held_plan["changes"] == 6
    md = main_plan.markdown()
    section = md.split("## Held plan (needs two approvers)\n", 1)[1].split("\n## ", 1)[0]
    assert "6 changes are in the held plan (applied only after two approvals)" in section
    assert "- 2026-11-10: 5 people, 5 removals and suspensions" in section
    assert "- Movers: 5" in section and "- Drift (changed outside the process): 1" in section
    assert "mover0" not in md and "d1" not in md  # moved changes are not listed one by one
    assert "keeper" in md  # main's own changes are
    assert "## Held for bad HR data (never applied)\n\n- " in md and "badrow" in md
    assert "1 held for bad HR data and never applied" in md
    assert "not applied" not in md.replace("never applied", "")


def test_the_held_part_files_say_they_are_the_held_part_and_why(policy):
    from meridian_access import summary

    bad = people_on("2026-11-10", 5)
    main_plan, held = split_plan(synthetic([*bad, *people_on("2026-11-11", 1)]), policy)
    md = held.markdown()
    assert "held part of the access plan" in md and "two code-owner approvals" in md
    assert "over the limit" in md
    text = summary.template_summary(held)
    assert "held part of the plan" in text and "applied only after two approvals" in text
    main_text = summary.template_summary(main_plan)
    assert "5 changes are in the held plan (applied only after two approvals)" in main_text
    assert "not applied" not in main_text


def test_main_summary_names_bad_data_holds_separately_and_never_applied(policy):
    from meridian_access import summary

    hold = row("badrow", eid="H1", day="2026-11-12", hold="bad_data")
    only, none = split_plan(synthetic([row("x", day="2026-11-10")], held=[hold]), policy)
    assert none is None
    assert "1 changes are held for bad HR data and never applied" in summary.template_summary(only)


def test_a_bulk_import_alone_says_a_bulk_import_in_slack_and_plan_md(policy):
    from meridian_access import slack_mock

    bulk = [row(f"b{i}", eid=f"B{i}", day="2026-11-29", hold="bulk") for i in range(3)]
    block = row(
        "", eid="", action="block_two_approvers", type_="hris_mass_termination", system="hris"
    )
    main_plan, _ = split_plan(synthetic([block, *people_on("2026-11-10", 2)], held=bulk), policy)
    message = slack_mock.build_message(main_plan, "https://example.com/pr")
    text = message["blocks"][2]["text"]["text"]
    assert "Held plan (its own pull request): a bulk import, 4 changes" in text
    assert "needs two approvers" in text and "0 buckets" not in text
    assert "- a bulk import" in main_plan.markdown()


def test_the_summary_placeholder_for_the_held_plan_is_filled_and_checked(policy):
    from meridian_access import summary

    bad = people_on("2026-11-10", 5)
    main_plan, _ = split_plan(synthetic([*bad, *people_on("2026-11-11", 1)]), policy)
    assert summary.placeholder_values(main_plan)["held_plan"] == 5
    assert summary.placeholder_values(main_plan)["held"] == 0
    paragraph = (
        "{held_plan} changes are in the held plan and need approvals, {held} held for bad data."
    )
    assert summary.fill_summary(paragraph, main_plan) == (
        "5 changes are in the held plan and need approvals, 0 held for bad data."
    )
    assert summary.fill_summary("{held_planx} changes", main_plan) is None
    assert summary.fill_summary("5 changes are in the held plan", main_plan) is None  # raw digit


def test_the_cli_replaces_the_held_folder_before_the_main_files(world_dir, tmp_path, monkeypatch):
    from meridian_access import plan as plan_mod

    out = tmp_path / "plan"
    main(["--world", str(world_dir), "--out", str(out)])
    seen = []
    real_replace, real_rmtree = plan_mod.os.replace, plan_mod.shutil.rmtree
    monkeypatch.setattr(
        plan_mod.os, "replace", lambda a, b: (seen.append(("replace", b.name)), real_replace(a, b))
    )
    monkeypatch.setattr(
        plan_mod.shutil,
        "rmtree",
        lambda p, *a, **k: (seen.append(("rmtree", Path(p).name)), real_rmtree(p, *a, **k)),
    )
    main(["--world", str(world_dir), "--out", str(out)])
    held_steps = [i for i, (_, name) in enumerate(seen) if name == "held"]
    main_steps = [i for i, (_, name) in enumerate(seen) if name == "plan.json"]
    assert held_steps and main_steps and max(held_steps) < min(main_steps)
    # no held plan: the stale folder is removed before the main files too
    seen.clear()
    real = plan_mod.split_plan
    monkeypatch.setattr(plan_mod, "split_plan", lambda p, pol: (real(p, pol)[0], None))
    main(["--world", str(world_dir), "--out", str(out)])
    assert seen[0] == ("rmtree", "held") and not (out / "held").exists()
