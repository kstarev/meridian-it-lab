"""Approval counting: code owners only, the latest review only, the current commit only."""

import json

from meridian_access import approvals
from meridian_access.approvals import check, count_approvals, parse_codeowners, required_for
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


def changes(removals, block=False):
    rows = [{"action": "remove"} for _ in range(removals)]
    return rows + ([{"action": "block_two_approvers"}] if block else [])


def plan_claiming(n, rows):
    return {"approvals_required": n, "changes": rows}


def test_the_requirement_is_recomputed_from_the_changes_and_the_policy():
    n = POLICY.settings.blast_radius_n
    assert required_for(changes(n), POLICY) == POLICY.settings.approvals_default == 1
    assert required_for(changes(n + 1), POLICY) == POLICY.settings.approvals_over_n == 2
    assert required_for(changes(1, block=True), POLICY) == 2
    suspensions = [{"action": "suspend"}] * (n + 1)
    assert required_for(suspensions, POLICY) == 2
    assert required_for([{"action": "add"}] * 100 + [{"action": "flag"}] * 100, POLICY) == 1


def test_a_plan_claiming_one_approval_with_forty_removals_needs_two():
    one = [review("kstarev", "APPROVED")]
    ok, message = check(plan_claiming(1, changes(40)), one, OWNERS, HEAD, policy=POLICY)
    assert not ok and "needs 2 approvals, has 1" in message
    both = [*one, review("second", "APPROVED")]
    assert check(plan_claiming(1, changes(40)), both, OWNERS, HEAD, policy=POLICY)[0]


def test_a_plan_with_a_block_needs_two_whatever_it_claims():
    one = [review("kstarev", "APPROVED")]
    ok, message = check(plan_claiming(1, changes(0, block=True)), one, OWNERS, HEAD, policy=POLICY)
    assert not ok and "needs 2 approvals" in message


def test_check_compares_with_the_plans_requirement():
    plan = plan_claiming(2, [])
    one = [review("kstarev", "APPROVED")]
    ok, message = check(plan, one, OWNERS, HEAD, policy=POLICY)
    assert not ok and "needs 2 approvals, has 1" in message  # the plan can ask for more
    ok, message = check(plan, [*one, review("second", "APPROVED")], OWNERS, HEAD, policy=POLICY)
    assert ok
    assert check(plan_claiming(1, []), one, OWNERS, HEAD, policy=POLICY)[0]


def test_the_pull_request_author_cannot_approve_their_own_plan():
    reviews = [review("kstarev", "APPROVED"), review("second", "APPROVED")]
    assert count_approvals(reviews, OWNERS, HEAD) == 2
    assert count_approvals(reviews, OWNERS, HEAD, author="kstarev") == 1
    assert count_approvals(reviews, OWNERS, HEAD, author="@KStarev") == 1
    ok, _ = check(plan_claiming(1, []), reviews[:1], OWNERS, HEAD, policy=POLICY, author="kstarev")
    assert not ok


def test_codeowners_file_logins_are_read():
    text = "# c\n/access/policy/ @kstarev @org/team\n/access/plan/ @planner\n/other/ @nobody\n"
    assert parse_codeowners(text, "/access/policy/") == {"kstarev"}
    assert parse_codeowners(text) == {"planner"}  # the default is the plan path


def test_the_cli_exits_1_when_there_are_not_enough_approvals(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    reviews = tmp_path / "reviews.json"
    plan.write_text(json.dumps({"approvals_required": 2, "changes": []}))
    reviews.write_text(json.dumps([review("kstarev", "APPROVED")]))
    argv = ["--plan", str(plan), "--reviews", str(reviews), "--codeowners", "kstarev,second"]
    assert approvals.main([*argv, "--head-sha", HEAD]) == 1
    assert "needs 2 approvals" in capsys.readouterr().out
    reviews.write_text(json.dumps([review("kstarev", "APPROVED"), review("second", "APPROVED")]))
    assert approvals.main([*argv, "--head-sha", HEAD]) == 0
    assert approvals.main([*argv, "--head-sha", OLD]) == 1  # a push made both stale
    # the author never counts, and the plan file cannot lower the requirement
    assert approvals.main([*argv, "--head-sha", HEAD, "--author", "second"]) == 1
    plan.write_text(json.dumps({"approvals_required": 1, "changes": changes(40)}))
    reviews.write_text(json.dumps([review("kstarev", "APPROVED")]))
    assert approvals.main([*argv, "--head-sha", HEAD]) == 1
