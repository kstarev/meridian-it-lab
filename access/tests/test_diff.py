"""The diff rules, on small hand-made scenarios (the tuning-world score is in test_plan.py)."""

import dataclasses

import pytest

from meridian_access.collect import ActualState
from meridian_access.desired import desired_state
from meridian_access.diff import build_changes
from meridian_access.match import MatchResult, match_accounts
from meridian_access.models import Account, LogEvent
from meridian_dataset.access.model import HrisEvent, HrisRecord

TITLE = "Backend Engineer"  # a title the reviewed map knows (role eng-backend)


def rec(eid, given, family, **kw):
    kw.setdefault("work_email", f"{given.lower()}.{family.lower()}@meridian.example")
    base = {
        "employee_id": eid,
        "given_name": given,
        "family_name": family,
        "preferred_name": "",
        "personal_email": f"{given.lower()}@mail.example",
        "job_title": TITLE,
        "department": "Engineering",
        "team": "Core",
        "manager_id": "",
        "country": "DE",
        "hub": "x",
        "employment_type": "employee",
        "status": "active",
        "start_date": "2025-01-01",
        "end_date": "",
        "leave_start": "",
        "leave_end": "",
    }
    base.update(kw)
    return HrisRecord(**base)


def ev(eid, kind, day=10, source="hr", **fields):
    return HrisEvent(day, f"2026-11-{day + 1:02d}", eid, kind, tuple(fields.items()), source)


def okta(login, state="active"):
    return Account("okta", login, email=login, state=state)


def assignment(login, app, role="member"):
    return Account("okta", login, email=login, app_id=app, role=role)


def row(app, account, **kw):
    kw.setdefault("email", account if "@" in account else "")
    return Account(app, account, app_id=app, **kw)


def run(policy, records, accounts, events=(), log=(), map_entries=(), pol=None):
    pol = pol or policy
    desired = desired_state(records, events, pol)
    actual = ActualState(tuple(accounts), tuple(log), None)
    matches = match_accounts(actual.accounts, records, pol)
    return build_changes(
        desired, actual, matches, pol, records=records, events=events, map_entries=map_entries
    )


def kinds(result):
    return sorted((c.type, c.system, c.app_id, c.action) for c in result.changes)


@pytest.fixture(scope="module")
def non_sso(policy):
    return sorted(a.id for a in policy.apps.values() if not a.sso)


@pytest.fixture(scope="module")
def sso(policy):
    return sorted(a.id for a in policy.apps.values() if a.sso)


def test_a_leaver_gets_suspend_remove_and_flag(policy, non_sso, sso):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-10")
    mail = r.work_email
    result = run(
        policy,
        [r],
        [
            okta(mail),
            assignment(mail, sso[0]),
            Account("google", mail, email=mail),
            Account("slack", "ada.stone", email=mail),
            row(non_sso[0], mail),
        ],
        [ev("E1", "terminate")],
    )
    assert kinds(result) == sorted(
        [
            ("leaver_access_left", "okta", "", "suspend"),
            ("leaver_access_left", "okta", sso[0], "remove"),
            ("leaver_access_left", "google", "", "suspend"),
            ("leaver_access_left", "slack", "", "remove"),
            ("leaver_access_left", non_sso[0], non_sso[0], "flag"),
        ]
    )


def test_an_already_suspended_account_is_left_alone(policy):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-10")
    mail = r.work_email
    acct = [
        okta(mail, "suspended"),
        dataclasses.replace(Account("google", mail, email=mail), state="suspended"),
    ]
    assert kinds(run(policy, [r], acct, [ev("E1", "terminate")])) == []


def test_a_former_employee_is_an_orphan_with_the_same_actions(policy):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-03-10")
    result = run(policy, [r], [okta(r.work_email)])
    assert kinds(result) == [("orphaned_account", "okta", "", "suspend")]
    assert result.changes[0].employee_id == "E1"


def test_a_contractor_past_end_is_removed_not_suspended(policy, non_sso):
    r = rec("E1", "Ada", "Stone", employment_type="contractor", end_date="2026-10-01")
    mail = r.work_email
    result = run(
        policy,
        [r],
        [okta(mail), Account("google", mail, email=mail), row(non_sso[0], mail)],
    )
    assert kinds(result) == sorted(
        [
            ("contractor_past_end", "okta", "", "remove"),
            ("contractor_past_end", "google", "", "remove"),
            ("contractor_past_end", non_sso[0], non_sso[0], "flag"),
        ]
    )


def test_a_person_on_leave_is_suspended_never_removed(policy, sso):
    r = rec("E1", "Ada", "Stone", status="on_leave", leave_start="2026-11-05")
    mail = r.work_email
    result = run(
        policy,
        [r],
        [okta(mail), assignment(mail, sso[0]), Account("slack", "ada.stone", email=mail)],
    )
    assert kinds(result) == sorted(
        [
            ("leave_of_absence", "okta", "", "suspend"),
            ("leave_of_absence", "slack", "", "suspend"),
        ]
    )


def test_unmatched_accounts_are_orphans_and_ambiguous_ones_are_warnings(policy, non_sso, sso):
    a, b = (
        rec("E1", "Sam", "Kim", work_email="sam.kim@meridian.example"),
        rec("E2", "Sam", "Kim", work_email="sam.kim2@meridian.example"),
    )
    ghost = "ghost.user@meridian.example"
    result = run(
        policy,
        [a, b],
        [
            okta(a.work_email),
            okta(b.work_email),
            okta(ghost),
            assignment(ghost, sso[0]),
            Account("slack", "ghost.user", email=ghost),
            row(non_sso[0], "sam.kim", email=""),  # two namesakes: ambiguous
            row(non_sso[0], ghost),
        ],
    )
    orphans = [c for c in result.changes if c.type == "orphaned_account"]
    assert sorted((c.system, c.app_id, c.action) for c in orphans) == sorted(
        [
            ("okta", "", "remove"),
            ("okta", sso[0], "remove"),
            ("slack", "", "remove"),
            (non_sso[0], non_sso[0], "flag"),
        ]
    )
    assert all(c.employee_id == "" for c in orphans)
    assert any(
        "ambiguous owner, needs a map entry" in w and "sam.kim" in w for w in result.warnings
    )
    assert not any(c.account == "sam.kim" for c in result.changes)


def test_break_glass_accounts_are_never_touched_and_a_pattern_match_warns(policy, sso):
    listed = "breakglass-01@meridian.example"
    patterned = "breakglass-77@meridian.example"
    result = run(
        policy,
        [],
        [
            okta(listed),
            assignment(listed, sso[0], "admin"),
            Account("google", listed, email=listed),
            okta(patterned),
        ],
    )
    assert kinds(result) == sorted(
        [
            ("break_glass", "okta", "", "never_touch"),
            ("break_glass", "google", "", "never_touch"),
            ("break_glass", "okta", "", "never_touch"),
        ]
    )
    assert result.warnings == (
        "breakglass-77@meridian.example matches the break-glass pattern but isn't listed",
    )


def _bulk(policy, n_people, limit):
    pol = dataclasses.replace(
        policy, settings=dataclasses.replace(policy.settings, blast_radius_n=limit)
    )
    records, events, accounts = [], [], []
    for i in range(n_people):
        r = rec(f"E{i}", "Pat", f"Lee{chr(97 + i)}", status="terminated", end_date="2026-11-28")
        records.append(r)
        events.append(ev(r.employee_id, "terminate", 27, "bulk_import"))
        accounts += [okta(r.work_email), Account("google", r.work_email, email=r.work_email)]
    return run(policy, records, accounts, events, pol=pol)


def test_a_bulk_hr_change_over_the_limit_is_held_as_one_finding(policy):
    result = _bulk(policy, 3, 4)  # 3 people x 2 suspensions = 6 > 4
    assert [(c.type, c.system, c.action, c.account, c.employee_id) for c in result.changes] == [
        ("hris_mass_termination", "hris", "block_two_approvers", "", "")
    ]
    assert len(result.held) == 6


def test_a_bulk_hr_change_within_the_limit_is_treated_as_normal_leavers(policy):
    result = _bulk(policy, 3, 6)
    assert {c.type for c in result.changes} == {"leaver_access_left"}
    assert result.held == ()


def test_hr_data_errors(policy):
    records = [
        rec("E1", "Ada", "Stone", start_date="2026-05-01", end_date="2026-01-01"),
        rec("E2", "Bo", "Reed", status="terminated", end_date=""),
        rec("E3", "Cy", "Moss", status="on_leave", leave_start=""),
        rec("E4", "Di", "Fox"),
        rec("E4", "Di", "Fox", work_email="di.fox2@meridian.example"),
    ]
    result = run(policy, records, [])
    errors = [(c.employee_id, c.account, c.system, c.action) for c in result.changes]
    assert errors == [
        ("E1", "E1", "hris", "flag"),
        ("E2", "E2", "hris", "flag"),
        ("E3", "E3", "hris", "flag"),
        ("E4", "E4", "hris", "flag"),
    ]
    assert {c.type for c in result.changes} == {"hr_data_error"}


def test_only_reviewed_map_entries_are_used(policy, non_sso):
    r = rec("E1", "Ada", "Stone")
    mystery = row(non_sso[0], "zz9", email="")
    key = f"{non_sso[0]}/{non_sso[0]}/zz9"
    entry = {"kind": "account", "key": key, "value": "E1", "source": "ai", "reviewed": False}
    base = [okta(r.work_email), mystery]
    plain = run(policy, [r], base, map_entries=[entry])
    assert [c.type for c in plain.changes] == ["orphaned_account"]  # not placed on a guess
    assert [e["key"] for e in plain.suggested_map_entries] == [key]
    reviewed = run(policy, [r], base, map_entries=[{**entry, "reviewed": True}])
    assert [c.type for c in reviewed.changes] != ["orphaned_account"]
    assert reviewed.suggested_map_entries == ()


def test_an_ai_sourced_match_without_a_reviewed_entry_is_dropped(policy, non_sso):
    from meridian_access.match import Match

    r = rec("E1", "Ada", "Stone")
    acc = row(non_sso[0], "zz9", email="")
    desired = desired_state([r], [], policy)
    actual = ActualState((acc,), (), None)
    ai = MatchResult({acc.key: Match("E1", "ai_suggestion", 0.8)}, [], [])
    result = build_changes(desired, actual, ai, policy, records=[r])
    assert [(c.type, c.employee_id) for c in result.changes] == [("orphaned_account", "")]


def test_movers_drift_and_stale_access(policy, sso):
    r = rec("E1", "Ada", "Stone")
    mail = r.work_email
    role_apps = set(policy.roles["eng-backend"].sso_apps)
    extra = [a for a in sso if a not in policy.birthright_employee and a not in role_apps]
    stale, by_hand = extra[0], extra[1]
    log = [
        LogEvent(
            "u1",
            "2026-11-10",
            "application.user_membership.add",
            "x",
            "User",
            "admin console",
            "0oa1",
            by_hand,
            "00u1",
            mail,
        ),
    ]
    wanted = [assignment(mail, a) for a in {*policy.birthright_employee, *role_apps}]
    accounts = [okta(mail), *wanted, assignment(mail, stale), assignment(mail, by_hand)]
    moved = run(policy, [r], accounts, [ev("E1", "move", department="Product")], log)
    assert [k for k in kinds(moved) if k[1] == "okta"] == sorted(
        [
            ("mover_stale_access", "okta", stale, "remove"),
            ("drift", "okta", by_hand, "remove"),
        ]
    )
    reasons = {c.type: c.reason for c in moved.changes}
    assert reasons["mover_stale_access"] == "moved to Product on 2026-11-11"
    still = run(policy, [r], accounts, [], log)
    assert {c.type for c in still.changes} == {"drift"}
    assert {c.action for c in still.changes if c.app_id == stale} == {"flag"}


def test_an_admin_role_raised_by_hand_on_a_desired_app_is_flagged(policy):
    r = rec("E1", "Ada", "Stone")
    mail = r.work_email
    app = policy.birthright_employee[0]
    log = [
        LogEvent(
            "u1",
            "2026-11-10",
            "application.user_membership.change_privilege",
            "x",
            "User",
            "admin console",
            "0oa1",
            app,
            "00u1",
            mail,
        ),
    ]
    result = run(policy, [r], [okta(mail), assignment(mail, app, "admin")], log=log)
    assert kinds(result) == [("drift", "okta", app, "flag")]


def test_a_missing_app_is_added_for_movers_and_only_a_warning_for_others(policy):
    r = rec("E1", "Ada", "Stone")
    mail = r.work_email
    accounts = [okta(mail)]  # no app assignments at all: every birthright app is missing
    n = len({*policy.birthright_employee, *policy.roles["eng-backend"].sso_apps})
    plain = run(policy, [r], accounts)
    assert plain.changes == ()
    assert len([w for w in plain.warnings if "is missing" in w]) == n
    moved = run(policy, [r], accounts, [ev("E1", "move", department="Sales")])
    sso_adds = [c for c in moved.changes if c.system == "okta"]
    assert {(c.type, c.action) for c in moved.changes} == {("mover_stale_access", "add")}
    assert len(sso_adds) == n


def test_a_mover_missing_a_username_only_export_gets_the_username_the_export_uses(policy):
    titled = next(
        (t, e.role) for t, e in policy.title_map.items() if policy.roles[e.role].other_apps
    )
    role = policy.roles[titled[1]]
    app = role.other_apps[0]
    mover = rec("E9", "Ada", "Stone", work_email="ada.stone2@meridian.example", job_title=titled[0])
    others = [
        rec("E1", "Bob", "Jones", job_title=titled[0]),
        rec("E2", "Cal", "Reed", job_title=titled[0]),
    ]
    rows = [
        row(app, "bjones", given="Bob", family="Jones"),
        row(app, "creed", given="Cal", family="Reed"),
        row(app, "astone2", given="Zed", family="Nobody"),  # the name an add would collide with
    ]
    accounts = [okta(p.work_email) for p in (mover, *others)] + rows
    result = run(policy, [mover, *others], accounts, [ev("E9", "move", department="Sales")])
    adds = [c for c in result.changes if c.system == app and c.action == "add"]
    assert [(c.employee_id, c.account) for c in adds] == [("E9", "astone22")]


def test_a_rehired_persons_rows_under_the_old_identity_are_flagged(policy, non_sso):
    new = rec(
        "E2", "Ada", "Stone", work_email="ada.stone@meridian.example", start_date="2026-11-03"
    )
    old = rec(
        "E1",
        "Ada",
        "Stone",
        work_email="ada.old@meridian.example",
        status="terminated",
        end_date="2026-04-01",
    )
    # same address on both records: the old record is superseded and owns nothing
    old_same = dataclasses.replace(old, work_email=new.work_email)
    app = non_sso[0]
    result = run(
        policy,
        [old_same, new],
        [okta(new.work_email), row(app, new.work_email)],
        [ev("E2", "rehire", 2, work_email=new.work_email)],
    )
    assert kinds(result) == [("rehire", app, app, "flag")]
    assert result.changes[0].employee_id == "E2"


def test_the_output_order_is_fixed(policy, sso):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-10")
    mail = r.work_email
    accounts = [okta(mail), assignment(mail, sso[1]), assignment(mail, sso[0])]
    one = run(policy, [r], accounts, [ev("E1", "terminate")])
    two = run(policy, [r], list(reversed(accounts)), [ev("E1", "terminate")])
    assert one == two


# ---------------------------------------------------------------- review fixes: hold or warn


def run_with(policy, records, accounts, matches, events=(), log=(), map_entries=(), pol=None):
    pol = pol or policy
    desired = desired_state(records, events, pol)
    actual = ActualState(tuple(accounts), tuple(log), None)
    return build_changes(
        desired, actual, matches, pol, records=records, events=events, map_entries=map_entries
    )


def owned_by(accounts, eid, rule="exact_email"):
    from meridian_access.match import Match

    return MatchResult({a.key: Match(eid, rule, 1.0) for a in accounts}, [], [])


def test_an_unmapped_title_changes_nothing_and_warns(policy, sso, non_sso):
    r = rec("E1", "Ada", "Stone", job_title="Chief Snack Officer")
    mail = r.work_email
    accounts = [okta(mail), assignment(mail, sso[0]), row(non_sso[0], mail)]
    plain = run(policy, [r], accounts)
    assert plain.changes == ()
    assert "unmapped title 'Chief Snack Officer' for E1: needs a reviewed title-map entry" in (
        plain.warnings
    )
    assert any("no reviewed title-map entry" in w for w in plain.warnings)
    moved = run(policy, [r], accounts, [ev("E1", "move", department="Sales")])
    assert moved.changes == ()  # a mover whose new title is unmapped: no adds, no removals


def test_no_evidence_drift_is_flagged_not_removed_and_group_assignments_are_left(policy, sso):
    r = rec("E1", "Ada", "Stone")
    mail = r.work_email
    wanted = set(policy.birthright_employee) | set(policy.roles["eng-backend"].sso_apps)
    extra = [a for a in sso if a not in wanted]
    base = [okta(mail), *(assignment(mail, a) for a in wanted)]
    flagged = run(policy, [r], [*base, assignment(mail, extra[0])])
    assert [(c.type, c.action) for c in flagged.changes] == [("drift", "flag")]
    grouped = dataclasses.replace(assignment(mail, extra[0]), scope="GROUP")
    left = run(policy, [r], [*base, grouped])
    assert left.changes == ()
    assert any("group assignment outside role" in w for w in left.warnings)
    log = [
        LogEvent(
            "u1",
            "2026-11-10",
            "application.user_membership.add",
            "x",
            "User",
            "admin console",
            "0oa1",
            extra[0],
            "00u1",
            mail,
        )
    ]
    by_hand = run(policy, [r], [*base, grouped], log=log)
    assert [(c.type, c.action) for c in by_hand.changes] == [("drift", "remove")]


def test_bad_hr_data_holds_the_person_and_keeps_the_flag(policy, sso):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="")  # no end date
    mail = r.work_email
    result = run(policy, [r], [okta(mail), assignment(mail, sso[0])], [ev("E1", "terminate")])
    assert [(c.type, c.employee_id) for c in result.changes] == [("hr_data_error", "E1")]
    assert {c.type for c in result.held} == {"leaver_access_left"}
    assert all("held: HR data error" in c.reason for c in result.held)


def test_a_termination_after_the_plan_date_is_a_notice_period_and_is_held(policy):
    r = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-12-10")
    mail = r.work_email
    desired = desired_state([r], [ev("E1", "terminate")], policy)
    assert desired.people[0].category == "active"
    assert desired.people[0].notice_until == "2026-12-10"
    stray = assignment(mail, "zzz-unlisted-app")
    result = run(policy, [r], [okta(mail), stray])
    assert result.changes == ()
    assert [c.type for c in result.held] == ["drift"]
    assert "held: notice period until 2026-12-10" in result.held[0].reason
    assert any("2026-12-10" in w and "treated as active" in w for w in result.warnings)


def test_a_duplicate_employee_id_holds_both_records(policy):
    one = rec("E4", "Di", "Fox", status="terminated", end_date="2026-11-10")
    two = rec("E4", "Di", "Fox", work_email="di.fox2@meridian.example")
    accounts = [okta(one.work_email), okta(two.work_email)]
    result = run(policy, [one, two], accounts, [ev("E4", "terminate")])
    errors = [c for c in result.changes if c.type == "hr_data_error"]
    assert len(errors) == 1
    assert one.work_email in errors[0].reason and two.work_email in errors[0].reason
    assert [c for c in result.changes if c.type != "hr_data_error"] == []
    assert result.held  # the accounts' changes are held, not applied


def test_hr_data_errors_make_one_change_per_record_with_the_problems_joined(policy):
    r = rec(
        "E1", "Ada", "Stone", status="terminated", start_date="2026-05-01", end_date="2026-01-01"
    )
    bad = rec("E2", "Bo", "Reed", status="terminated", start_date="2026-05-01", end_date="")
    result = run(policy, [r, bad], [])
    assert [c.employee_id for c in result.changes] == ["E1", "E2"]
    assert result.changes[0].reason == "end date is before the start date"
    both = rec(
        "E3", "Cy", "Moss", status="on_leave", start_date="2026-05-01", end_date="2026-01-01"
    )
    joined = run(policy, [both], []).changes[0].reason
    assert joined == "end date is before the start date; on leave with no leave start"


def _hr_batch(policy, n_people, limit, source="hr"):
    pol = dataclasses.replace(
        policy, settings=dataclasses.replace(policy.settings, blast_radius_n=limit)
    )
    records, events, accounts = [], [], []
    for i in range(n_people):
        r = rec(f"E{i}", "Pat", f"Lee{chr(97 + i)}", status="terminated", end_date="2026-11-20")
        records.append(r)
        events.append(ev(r.employee_id, "terminate", 19, source))
        accounts += [okta(r.work_email), Account("google", r.work_email, email=r.work_email)]
    return run(policy, records, accounts, events, pol=pol)


def test_a_big_batch_of_hr_terminations_on_one_day_is_held_as_one_finding(policy):
    from meridian_access.diff import MIN_BATCH_PEOPLE

    result = _hr_batch(policy, MIN_BATCH_PEOPLE + 1, 4)
    assert [(c.type, c.action) for c in result.changes] == [
        ("hris_mass_termination", "block_two_approvers")
    ]
    assert len(result.held) == 2 * (MIN_BATCH_PEOPLE + 1)
    few = _hr_batch(policy, 2, 1)  # two people are two ordinary leavers
    assert {c.type for c in few.changes} == {"leaver_access_left"}
    within = _hr_batch(policy, MIN_BATCH_PEOPLE + 1, 100)
    assert within.held == ()


BG = "breakglass-01@meridian.example"  # listed
BG_PATTERN = "breakglass-77@meridian.example"  # matches the pattern only


def test_no_change_can_touch_a_break_glass_account(policy):
    # a leaver wrongly matched to a break-glass login
    leaver = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-10")
    login = okta(BG)
    result = run_with(policy, [leaver], [login], owned_by([login], "E1"), [ev("E1", "terminate")])
    assert [c.type for c in result.changes] == []
    assert any("dropped: break-glass" in w for w in result.warnings)
    # a bare username, under its company address
    bare = Account("slack", "breakglass-01", email="")
    result = run_with(policy, [leaver], [bare], owned_by([bare], "E1"), [ev("E1", "terminate")])
    assert result.changes == ()
    # a Google alias
    google = Account("google", "x.y@meridian.example", email="x.y@meridian.example", aliases=(BG,))
    result = run_with(policy, [leaver], [google], owned_by([google], "E1"), [ev("E1", "terminate")])
    assert result.changes == ()
    # a reviewed map entry that places an unmatched break-glass login on a person
    ghost = okta(BG_PATTERN)
    entry = {"kind": "account", "key": "/".join(ghost.key), "value": "E1", "reviewed": True}
    unmatched = MatchResult({}, [ghost.key], [])
    result = run_with(
        policy, [leaver], [ghost], unmatched, [ev("E1", "terminate")], map_entries=[entry]
    )
    assert result.changes == ()
    # an orphan
    result = run_with(policy, [], [ghost], MatchResult({}, [ghost.key], []))
    assert result.changes == ()
    # a mover add whose person has a break-glass work email
    mover = rec("E2", "Bo", "Reed", work_email=BG_PATTERN)
    result = run(policy, [mover], [okta(BG_PATTERN)], [ev("E2", "move", department="Sales")])
    assert {c.type for c in result.changes} <= {"break_glass"}


def test_a_directory_orphan_that_looks_like_a_renamed_person_is_a_warning(policy):
    current = rec("E1", "Ada", "Okoye", work_email="ada.okoye@meridian.example")
    old_name = Account("okta", "ada.stone@meridian.example", email="ada.stone@meridian.example")
    unrelated = Account("okta", "zed.nobody@meridian.example", email="zed.nobody@meridian.example")
    named = Account(
        "google", "legacy-1@old.example", email="legacy-1@old.example", given="Ada", family="Okoye"
    )
    accounts = [old_name, unrelated, named]
    unmatched = MatchResult({}, [a.key for a in accounts], [])
    plain = run_with(policy, [current], accounts, unmatched)
    assert sorted(c.account for c in plain.changes) == [
        "ada.stone@meridian.example",
        "zed.nobody@meridian.example",
    ]
    assert sum("possible renamed person" in w for w in plain.warnings) == 1  # the name match
    # the old family name is not in the data: a name_change event marks the worker
    renamed = run_with(
        policy,
        [current],
        accounts,
        unmatched,
        [ev("E1", "name_change", family_name="Okoye")],
    )
    assert [c.account for c in renamed.changes] == ["zed.nobody@meridian.example"]
    assert sum("possible renamed person, needs a map entry" in w for w in renamed.warnings) == 2


def test_a_reviewed_entry_for_an_unknown_or_superseded_id_warns_and_places_nothing(policy, non_sso):
    r = rec("E1", "Ada", "Stone")
    mystery = row(non_sso[0], "zz9", email="")
    key = f"{non_sso[0]}/{non_sso[0]}/zz9"
    for target in ("E999",):
        entry = {"kind": "account", "key": key, "value": target, "reviewed": True}
        result = run(policy, [r], [okta(r.work_email), mystery], map_entries=[entry])
        assert not [c for c in result.changes if c.account == "zz9"]
        assert any("unknown or superseded" in w and target in w for w in result.warnings)
