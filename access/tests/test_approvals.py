"""Approval counting: code owners only, the latest review only, the current commit only."""

import json

import pytest

from meridian_access import approvals
from meridian_access.approvals import check, count_approvals, parse_codeowners, required_for
from meridian_access.plan import PLAN_VERSION
from meridian_access.policy import load_policy

POLICY = load_policy()

HEAD = "a" * 40
OLD = "b" * 40
OWNERS = {"kstarev", "second"}


def review(user, state, commit=HEAD, at="2026-11-15T10:00:00Z"):
    return {"user": {"login": user}, "state": state, "commit_id": commit, "submitted_at": at}


def test_an_owner_approval_on_the_head_commit_counts():
    assert count_approvals([review("kstarev", "APPROVED")], OWNERS, HEAD) == 1


def test_two_owners_count_twice_and_one_owner_twice_counts_once():
    both = [review("kstarev", "APPROVED"), review("second", "APPROVED")]
    assert count_approvals(both, OWNERS, HEAD) == 2
    twice = [
        review("kstarev", "APPROVED"),
        review("kstarev", "APPROVED", at="2026-11-15T11:00:00Z"),
    ]
    assert count_approvals(twice, OWNERS, HEAD) == 1


def test_a_stale_commit_does_not_count():
    assert count_approvals([review("kstarev", "APPROVED", commit=OLD)], OWNERS, HEAD) == 0


def test_a_non_owner_does_not_count():
    assert count_approvals([review("stranger", "APPROVED")], OWNERS, HEAD) == 0


def test_changes_requested_after_approved_removes_the_approval():
    reviews = [
        review("kstarev", "APPROVED", at="2026-11-15T10:00:00Z"),
        review("kstarev", "CHANGES_REQUESTED", at="2026-11-15T11:00:00Z"),
    ]
    assert count_approvals(reviews, OWNERS, HEAD) == 0
    reviews.append(review("kstarev", "APPROVED", at="2026-11-15T12:00:00Z"))
    assert count_approvals(reviews, OWNERS, HEAD) == 1


def test_a_comment_after_an_approval_does_not_withdraw_it_and_a_dismissal_does():
    reviews = [
        review("kstarev", "APPROVED"),
        review("kstarev", "COMMENTED", at="2026-11-15T11:00:00Z"),
    ]
    assert count_approvals(reviews, OWNERS, HEAD) == 1
    reviews.append(review("kstarev", "DISMISSED", at="2026-11-15T12:00:00Z"))
    assert count_approvals(reviews, OWNERS, HEAD) == 0


def test_logins_compare_without_case_or_at_sign():
    assert count_approvals([review("KStarev", "APPROVED")], {"@kstarev"}, HEAD) == 1


def changes(removals, block=False, people=None, day="2026-11-10"):
    """`removals` removals on `day`, spread over `people` employees (default: one each)."""
    people = people or removals
    rows = [
        {"action": "remove", "employee_id": f"E{i % people}", "cause_date": day}
        for i in range(removals)
    ]
    return rows + ([{"action": "block_two_approvers"}] if block else [])


MAIN = "access/plan/plan.json"
HELD = "access/plan/held/plan.json"


def plan_claiming(n, rows, part="main"):
    return {"version": PLAN_VERSION, "part": part, "approvals_required": n, "changes": rows}


def test_the_requirement_is_recomputed_from_the_changes_and_the_policy():
    s = POLICY.settings
    p, r = s.blast_radius_people, s.blast_radius_removals
    assert (p, r) == (5, 50)
    assert required_for(changes(49, people=p - 1), POLICY) == s.approvals_default == 1
    assert required_for(changes(p, people=p), POLICY) == s.approvals_over_n == 2
    assert required_for(changes(p + 3, people=p), POLICY) == 2
    assert required_for(changes(1, block=True), POLICY) == 2
    assert required_for([{"action": "suspend", "cause_date": "", "employee_id": "E1"}], POLICY) == 1
    assert required_for([{"action": "add"}] * 100 + [{"action": "flag"}] * 100, POLICY) == 1


def test_five_people_spread_over_five_days_need_one_approval():
    rows = [
        {"action": "remove", "employee_id": f"E{i}", "cause_date": f"2026-11-{10 + i}"}
        for i in range(5)
    ] * 2
    assert len(rows) == 10 and required_for(rows, POLICY) == 1


def test_fifty_removals_from_two_people_need_two_but_forty_nine_need_one():
    assert required_for(changes(50, people=2), POLICY) == 2
    assert required_for(changes(49, people=2), POLICY) == 1


def undated(people, each):
    return [
        {"action": "remove", "employee_id": f"E{p}", "cause_date": ""}
        for p in range(people)
        for _ in range(each)
    ]


def test_undated_removals_count_together_as_one_day():
    assert required_for(undated(6, 8), POLICY) == 2  # 48 removals, 6 people, no date
    assert required_for(undated(5, 1), POLICY) == 2  # the people leg alone
    assert required_for(undated(4, 8), POLICY) == 1  # 32 removals, 4 people
    # a day with 4 people plus the no-date bucket with 4 other people stay separate
    mixed = changes(4, people=4) + [
        {"action": "remove", "employee_id": f"X{i}", "cause_date": ""} for i in range(4)
    ]
    assert required_for(mixed, POLICY) == 1


def test_removals_with_no_owner_never_count_toward_people():
    orphans = [{"action": "remove", "employee_id": "", "cause_date": ""}] * 10
    assert required_for(orphans, POLICY) == 1
    no_owner = [{"action": "remove", "employee_id": "", "cause_date": "2026-11-10"}] * 10
    assert required_for(no_owner, POLICY) == 1
    assert required_for([{"action": "remove", "cause_date": ""}] * 10, POLICY) == 1


@pytest.mark.parametrize("bad", [None, 5, "garbage", "2026-13-40", "2026-1-5", ["2026-11-10"]])
def test_a_bad_cause_date_is_an_error_not_one(bad):
    rows = [{"action": "suspend", "employee_id": "E1", "cause_date": bad}]
    with pytest.raises(ValueError, match="cause_date"):
        required_for(rows, POLICY)
    ok, message = check(
        plan_claiming(1, rows),
        [review("kstarev", "APPROVED")],
        OWNERS,
        HEAD,
        policy=POLICY,
        plan_path=MAIN,
    )
    assert not ok and "cause_date" in message


def test_a_plan_of_another_version_is_refused_by_the_check():
    data = {**plan_claiming(1, []), "version": 1}
    ok, message = check(
        data, [review("kstarev", "APPROVED")], OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert not ok and "plan version 1, expected 2: build the plan again" in message
    no_version = {"approvals_required": 1, "changes": []}
    ok, message = check(
        no_version, [review("kstarev", "APPROVED")], OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert not ok and "plan version None, expected 2" in message


def test_flags_and_adds_never_count_toward_a_day():
    rows = [
        {"action": a, "employee_id": f"E{i}", "cause_date": "2026-11-10"}
        for i in range(6)
        for a in ("add", "flag")
    ]
    assert required_for(rows, POLICY) == 1


def test_a_change_without_cause_date_is_an_error_not_one():
    with pytest.raises(ValueError, match="cause_date"):
        required_for([{"action": "remove", "employee_id": "E1"}], POLICY)
    ok, message = check(
        plan_claiming(1, [{"action": "remove", "employee_id": "E1"}]),
        [review("kstarev", "APPROVED")],
        OWNERS,
        HEAD,
        policy=POLICY,
        plan_path=MAIN,
    )
    assert not ok and "cause_date" in message


def test_a_plan_claiming_one_approval_with_five_people_on_one_day_needs_two():
    one = [review("kstarev", "APPROVED")]
    rows = changes(5, people=5)
    ok, message = check(plan_claiming(1, rows), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)
    assert not ok and "needs 2 approvals, has 1" in message
    both = [*one, review("second", "APPROVED")]
    assert check(plan_claiming(1, rows), both, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)[0]


def test_forty_people_on_one_day_need_two_but_forty_removals_from_two_people_on_two_days_need_one():
    one = [review("kstarev", "APPROVED")]
    ok, message = check(
        plan_claiming(1, changes(40)), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert not ok and "needs 2 approvals, has 1" in message
    both = [*one, review("second", "APPROVED")]
    assert check(plan_claiming(1, changes(40)), both, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)[
        0
    ]
    two_days = [
        {"action": "remove", "employee_id": f"E{i % 2}", "cause_date": f"2026-11-{10 + i % 2}"}
        for i in range(40)
    ]
    assert required_for(two_days, POLICY) == 1
    assert check(plan_claiming(1, two_days), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)[0]


def test_a_plan_with_a_block_needs_two_whatever_it_claims():
    one = [review("kstarev", "APPROVED")]
    ok, message = check(
        plan_claiming(1, changes(0, block=True)), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert not ok and "needs 2 approvals" in message


def test_check_compares_with_the_plans_requirement():
    plan = plan_claiming(2, [])
    one = [review("kstarev", "APPROVED")]
    ok, message = check(plan, one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)
    assert not ok and "needs 2 approvals, has 1" in message  # the plan can ask for more
    ok, message = check(
        plan, [*one, review("second", "APPROVED")], OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert ok
    assert check(plan_claiming(1, []), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)[0]


def test_the_pull_request_author_cannot_approve_their_own_plan():
    reviews = [review("kstarev", "APPROVED"), review("second", "APPROVED")]
    assert count_approvals(reviews, OWNERS, HEAD) == 2
    assert count_approvals(reviews, OWNERS, HEAD, author="kstarev") == 1
    assert count_approvals(reviews, OWNERS, HEAD, author="@KStarev") == 1
    ok, _ = check(
        plan_claiming(1, []),
        reviews[:1],
        OWNERS,
        HEAD,
        policy=POLICY,
        plan_path=MAIN,
        author="kstarev",
    )
    assert not ok


def test_codeowners_file_logins_are_read():
    text = "# c\n/access/policy/ @kstarev @org/team\n/access/plan/ @planner\n/other/ @nobody\n"
    assert parse_codeowners(text, "/access/policy/") == {"kstarev"}
    assert parse_codeowners(text) == {"planner"}  # the default is the plan path


def test_the_cli_exits_1_when_there_are_not_enough_approvals(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    reviews = tmp_path / "reviews.json"
    plan.write_text(json.dumps(plan_claiming(2, [])))
    reviews.write_text(json.dumps([review("kstarev", "APPROVED")]))
    argv = [
        "--plan",
        str(plan),
        "--plan-path",
        MAIN,
        "--reviews",
        str(reviews),
        "--codeowners",
        "kstarev,second",
    ]
    assert approvals.main([*argv, "--head-sha", HEAD]) == 1
    assert "needs 2 approvals" in capsys.readouterr().out
    reviews.write_text(json.dumps([review("kstarev", "APPROVED"), review("second", "APPROVED")]))
    assert approvals.main([*argv, "--head-sha", HEAD]) == 0
    assert approvals.main([*argv, "--head-sha", OLD]) == 1  # a push made both stale
    # the author never counts, and the plan file cannot lower the requirement
    assert approvals.main([*argv, "--head-sha", HEAD, "--author", "second"]) == 1
    plan.write_text(json.dumps(plan_claiming(1, changes(40))))
    reviews.write_text(json.dumps([review("kstarev", "APPROVED")]))
    assert approvals.main([*argv, "--head-sha", HEAD]) == 1


def test_the_held_part_needs_two_whatever_the_file_says():
    one = [review("kstarev", "APPROVED")]
    both = [*one, review("second", "APPROVED")]
    held = plan_claiming(1, [], part="held")  # no changes, claims 1: still two
    ok, message = check(held, one, OWNERS, HEAD, policy=POLICY, plan_path=HELD)
    assert not ok and "needs 2 approvals, has 1" in message
    assert check(held, both, OWNERS, HEAD, policy=POLICY, plan_path=HELD)[0]


def test_the_part_must_be_main_or_held():
    one = [review("kstarev", "APPROVED")]
    for part in ("other", "", None):
        ok, message = check(
            plan_claiming(1, [], part=part), one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN
        )
        assert not ok and "unknown plan part" in message
    no_part = {"version": PLAN_VERSION, "approvals_required": 1, "changes": []}
    ok, message = check(no_part, one, OWNERS, HEAD, policy=POLICY, plan_path=MAIN)
    assert not ok and "unknown plan part" in message


def test_the_file_path_must_match_the_part():
    both = [review("kstarev", "APPROVED"), review("second", "APPROVED")]
    ok, message = check(
        plan_claiming(2, [], part="held"), both, OWNERS, HEAD, policy=POLICY, plan_path=MAIN
    )
    assert not ok and "held part" in message and HELD in message
    ok, message = check(
        plan_claiming(1, [], part="main"), both, OWNERS, HEAD, policy=POLICY, plan_path=HELD
    )
    assert not ok and "main part" in message and MAIN in message
    assert check(
        plan_claiming(1, [], part="main"), both, OWNERS, HEAD, policy=POLICY, plan_path="./" + MAIN
    )[0]


def test_the_cli_refuses_a_held_plan_read_from_the_main_path(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    reviews = tmp_path / "reviews.json"
    plan.write_text(json.dumps(plan_claiming(2, [], part="held")))
    reviews.write_text(json.dumps([review("kstarev", "APPROVED"), review("second", "APPROVED")]))
    argv = ["--plan", str(plan), "--reviews", str(reviews), "--codeowners", "kstarev,second"]
    argv += ["--head-sha", HEAD]
    assert approvals.main([*argv, "--plan-path", MAIN]) == 1
    assert "expected access/plan/held/plan.json" in capsys.readouterr().out
    assert approvals.main([*argv, "--plan-path", HELD]) == 0
