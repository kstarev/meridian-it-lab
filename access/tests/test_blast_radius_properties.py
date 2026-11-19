"""Property-based tests for the blast-radius limit (hypothesis; AAC-007).

The rule (limits.py, policy/settings.yaml): removals and suspensions are grouped by the day of
the HR event behind them, with one extra "no-date" bucket. A bucket with `blast_radius_people`
or more people, or `blast_radius_removals` or more removals and suspensions, is over the limit.
A plan with an over-limit bucket, or a `block_two_approvers` entry, needs `approvals_over_n`
approvals; any other plan `approvals_default`. The split sends over-limit buckets to a held
plan and keeps the rest in the main plan, losing and duplicating nothing.

Here the limits vary too, so the edges (one below, at, one above) are hit often. Each property
is checked against a plain count written here, not against the code under test. Made-up
changes only; no world, no network. The runs are fixed (derandomize) and keep no example
database.
"""

import collections
import dataclasses
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.approvals import PART_PATHS, check, count_approvals, required_for
from meridian_access.limits import NO_DATE, Bucket, bucket_key, buckets, over_limit
from meridian_access.plan import Plan, split_plan
from meridian_access.policy.load import load_policy

SETTINGS = settings(derandomize=True, max_examples=200, database=None, deadline=None)
BASE = load_policy()
REMOVING = ("remove", "suspend")
OTHER_ACTIONS = ("add", "flag", "never_touch")
DAYS = ("2026-11-03", "2026-11-04", "")
HEAD = "c" * 40


def policy_with(people: int, removals: int, over_n: int = 2, default: int = 1):
    s = dataclasses.replace(
        BASE.settings,
        blast_radius_people=people,
        blast_radius_removals=removals,
        approvals_over_n=over_n,
        approvals_default=default,
    )
    return dataclasses.replace(BASE, settings=s)


limits = st.tuples(st.integers(1, 5), st.integers(1, 7))


def change(i, action="remove", day="", eid="", type_="leaver_access_left", **kw):
    return {
        "type": type_,
        "employee_id": eid,
        "account": f"acct-{i}@meridian.example",
        "system": "okta",
        "app_id": "",
        "app_name": "",
        "action": action,
        "reason": "test",
        "evidence": [],
        "cause_date": day,
        "hold": "",
        **kw,
    }


@st.composite
def change_lists(draw, max_size=14, blocks=False):
    actions = REMOVING + OTHER_ACTIONS + (("block_two_approvers",) if blocks else ())
    rows = draw(
        st.lists(
            st.tuples(
                st.sampled_from(actions),
                st.sampled_from(DAYS),
                st.sampled_from(("", "E1", "E2", "E3", "E4", "E5", "E6")),
            ),
            max_size=max_size,
        )
    )
    return [change(i, a, d, e) for i, (a, d, e) in enumerate(rows)]


def oracle_over(changes, people_limit, removals_limit) -> set[str]:
    """The over-limit buckets, counted by hand."""
    ids: dict[str, set[str]] = collections.defaultdict(set)
    counts: collections.Counter[str] = collections.Counter()
    for c in changes:
        if c["action"] not in REMOVING:
            continue
        key = c["cause_date"] or NO_DATE
        counts[key] += 1
        if c["employee_id"]:
            ids[key].add(c["employee_id"])
    return {k for k in counts if len(ids[k]) >= people_limit or counts[k] >= removals_limit}


# --- the count --------------------------------------------------------------------------------


@SETTINGS
@given(change_lists(), limits)
def test_buckets_count_distinct_people_and_removals_per_cause_day(changes, lim):
    got = buckets(changes)
    removing = [c for c in changes if c["action"] in REMOVING]
    assert set(got) == {c["cause_date"] or NO_DATE for c in removing}
    for key, b in got.items():
        mine = [c for c in removing if (c["cause_date"] or NO_DATE) == key]
        assert b.removals == len(mine)
        assert b.people == frozenset(c["employee_id"] for c in mine if c["employee_id"])
    assert list(got) == sorted(got)
    policy = policy_with(*lim)
    assert {k for k, b in got.items() if over_limit(b, policy.settings)} == oracle_over(
        changes, *lim
    )


@SETTINGS
@given(st.integers(0, 8), st.integers(0, 8), limits)
def test_over_limit_is_at_or_above_either_number(people, removals, lim):
    bucket = Bucket(frozenset(f"E{i}" for i in range(people)), removals)
    s = policy_with(*lim).settings
    assert over_limit(bucket, s) == (people >= lim[0] or removals >= lim[1])


# --- the approvals a plan needs ---------------------------------------------------------------


@SETTINGS
@given(change_lists(blocks=True), limits, st.integers(1, 3), st.integers(2, 4))
def test_required_for_is_the_larger_number_exactly_when_a_bucket_is_over(
    changes, lim, default, over_n
):
    policy = policy_with(*lim, over_n=over_n, default=default)
    blocked = any(c["action"] == "block_two_approvers" for c in changes)
    want = over_n if blocked or oracle_over(changes, *lim) else default
    assert required_for(changes, policy) == want
    assert required_for(list(reversed(changes)), policy) == want


@SETTINGS
@given(change_lists(), change_lists(), limits)
def test_more_changes_never_need_fewer_approvals(base, extra, lim):
    policy = policy_with(*lim)
    renumbered = [{**c, "account": f"x{c['account']}"} for c in extra]
    assert required_for([*base, *renumbered], policy) >= required_for(base, policy)


@SETTINGS
@given(change_lists(), st.lists(st.sampled_from(OTHER_ACTIONS), max_size=60), limits)
def test_adds_flags_and_report_only_entries_never_count(changes, others, lim):
    policy = policy_with(*lim)
    padding = [change(100 + i, a, "2026-11-03", f"E{i}") for i, a in enumerate(others)]
    assert required_for([*changes, *padding], policy) == required_for(changes, policy)


@SETTINGS
@given(st.integers(1, 5), st.integers(1, 7), st.sampled_from(DAYS))
def test_one_day_at_the_people_limit_needs_two_and_one_below_needs_one(people, removals, day):
    policy = policy_with(people, removals + 100)
    at = [change(i, "suspend", day, f"E{i}") for i in range(people)]
    assert required_for(at, policy) == 2
    assert required_for(at[:-1], policy) == 1
    # the same people's removals spread over two days, one per day, are never over a limit of 2+
    if people >= 2:
        spread = [{**c, "cause_date": DAYS[i % 2]} for i, c in enumerate(at[:2])]
        assert required_for(spread, policy_with(2, 100)) == 1


@SETTINGS
@given(st.integers(1, 7), st.sampled_from(DAYS))
def test_removals_without_a_person_count_toward_removals_only(removals, day):
    policy = policy_with(1, removals)
    orphans = [change(i, "remove", day, "") for i in range(removals)]
    assert required_for(orphans, policy) == 2
    assert required_for(orphans[:-1], policy) == 1


# --- the cause day must be well formed --------------------------------------------------------

BAD_DAYS = (
    "2026-13-01",
    "2026-02-30",
    "26-11-03",
    "2026/11/03",
    "2026-11-03T00:00",
    " 2026-11-03",
    "\u0662\u0660\u0662\u0666-\u0661\u0661-\u0660\u0663",  # Arabic-Indic digits
    "no-date",
)


@pytest.mark.parametrize("bad", [*BAD_DAYS, None, 20261103, ["2026-11-03"]])
def test_a_bad_cause_date_is_refused_never_guessed(bad):
    with pytest.raises(ValueError):
        bucket_key({"cause_date": bad})
    with pytest.raises(ValueError):
        required_for([change(0, "remove", "", "E1") | {"cause_date": bad}], BASE)


def test_a_missing_cause_date_is_refused():
    row = change(0)
    del row["cause_date"]
    with pytest.raises(ValueError, match="no cause_date"):
        bucket_key(row)


@SETTINGS
@given(st.dates())
def test_every_real_day_is_its_own_bucket(day):
    text = day.isoformat()
    if len(text) != 10:  # years before 1000 or after 9999 are not YYYY
        return
    assert bucket_key({"cause_date": text}) == text
    assert bucket_key({"cause_date": ""}) == NO_DATE


# --- the split ---------------------------------------------------------------------------------


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
            "warnings": [],
            "suggested_map_entries": [],
            "counts": {},
            "approvals_required": 2,
            "manual_tasks": [],
        }
    )


@st.composite
def plans(draw):
    changes = draw(change_lists(max_size=16, blocks=True))
    # break-glass and HR-data-error entries stay in main whatever their bucket
    extra = draw(
        st.lists(
            st.sampled_from(
                (
                    ("break_glass", "never_touch"),
                    ("hr_data_error", "flag"),
                )
            ),
            max_size=3,
        )
    )
    changes += [change(200 + i, a, "2026-11-03", "E1", type_=t) for i, (t, a) in enumerate(extra)]
    bulk = [
        change(300 + i, "remove", d, f"B{i}", hold="bulk")
        for i, d in enumerate(draw(st.lists(st.sampled_from(DAYS), max_size=3)))
    ]
    if bulk:  # the diff always adds one block entry next to the changes a bulk import holds
        changes.append(
            change(299, "block_two_approvers", "", "", type_="hris_mass_termination", account="")
        )
    bad = [
        change(400 + i, "remove", "", f"H{i}", hold="bad_data")
        for i in range(draw(st.integers(0, 2)))
    ]
    return changes, bulk, bad


def _same(c: dict[str, Any]) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in c.items() if k not in ("hold", "reason")))


@SETTINGS
@given(plans(), limits)
def test_the_split_loses_nothing_and_main_is_never_over_the_limit(plan, lim):
    changes, bulk, bad = plan
    policy = policy_with(*lim)
    main, held = split_plan(synthetic(changes, [*bulk, *bad]), policy)
    main_rows = main.data["changes"]
    held_rows = held.data["changes"] if held else []
    # a partition: every change and every bulk-held change once, in one of the two plans
    assert sorted(map(_same, [*main_rows, *held_rows])) == sorted(map(_same, [*changes, *bulk]))
    # main needs one approval and has nothing from an over-limit bucket
    over = oracle_over([*changes, *bulk], *lim)
    assert required_for(main_rows, policy) == policy.settings.approvals_default
    assert main.data["approvals_required"] == policy.settings.approvals_default
    assert main.data["part"] == "main"
    for c in main_rows:
        assert c["action"] != "block_two_approvers"
        if c["type"] not in ("break_glass", "hr_data_error"):
            assert (c["cause_date"] or NO_DATE) not in over
    # bad data is never applied by either plan: it stays in main's held list only
    assert all(c.get("hold") != "bad_data" for c in held_rows)
    assert sum(c.get("hold") == "bad_data" for c in main.data["held"]) == len(bad)
    # the held plan: there exactly when something must wait for two approvals
    must_wait = bool(bulk) or any(c["action"] == "block_two_approvers" for c in changes)
    must_wait = must_wait or any(
        (c["cause_date"] or NO_DATE) in over
        for c in changes
        if c["type"] not in ("break_glass", "hr_data_error")
    )
    assert (held is not None) == must_wait
    if held is not None:
        assert held.data["part"] == "held"
        assert held.data["approvals_required"] == policy.settings.approvals_over_n
        assert all(c["hold"] == "" for c in held_rows)
        assert main.data["held_plan"]["changes"] == len(held_rows)
        assert {b["day"] for b in held.data["buckets"]} == over
    else:
        assert main.data["held_plan"] is None


# --- the approvals check -----------------------------------------------------------------------

logins = st.sampled_from(("kstarev", "KStarev", "@kstarev", "alice", "bob", "mallory"))
states = st.sampled_from(("APPROVED", "approved", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"))
reviews = st.lists(
    st.tuples(logins, states, st.sampled_from((HEAD, "d" * 40)), st.integers(0, 5)),
    max_size=8,
)


def review(login, state, commit, at):
    return {
        "user": {"login": login},
        "state": state,
        "commit_id": commit,
        "submitted_at": f"2026-11-19T0{at}:00:00Z",
    }


def oracle_approvals(rows, owners, author):
    owners = {o.lstrip("@").casefold() for o in owners} - {author.lstrip("@").casefold()}
    latest = {}
    for _i, (login, state, commit, _at) in sorted(enumerate(rows), key=lambda r: (r[1][3], r[0])):
        if state.upper() in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login.lstrip("@").casefold()] = (state.upper(), commit)
    return sum(
        1
        for who, (state, commit) in latest.items()
        if who in owners and state == "APPROVED" and commit == HEAD
    )


@SETTINGS
@given(reviews, st.sampled_from(({"kstarev"}, {"kstarev", "alice"}, {"@Alice", "bob"})), logins)
def test_only_a_code_owner_s_latest_approval_on_the_head_counts_never_the_author(
    rows, owners, author
):
    got = count_approvals([review(*r) for r in rows], owners, HEAD, author)
    assert got == oracle_approvals(rows, owners, author)


@SETTINGS
@given(change_lists(blocks=True), st.integers(0, 3), st.sampled_from(("main", "held")), limits)
def test_the_check_needs_the_larger_of_the_file_and_the_rule_and_two_for_held(
    changes, claimed, part, lim
):
    policy = policy_with(*lim)
    data = {**synthetic(changes).data, "part": part, "approvals_required": claimed}
    need = max(claimed, required_for(changes, policy))
    if part == "held":
        need = max(need, policy.settings.approvals_over_n)
    owners = {"alice", "bob", "carol"}
    for have in range(4):
        rows = [review(o, "APPROVED", HEAD, 1) for o in sorted(owners)[:have]]
        ok, _ = check(data, rows, owners, HEAD, policy=policy, plan_path=PART_PATHS[part])
        assert ok == (have >= need)
    # read from the other part's path, it never passes
    other = PART_PATHS["held" if part == "main" else "main"]
    rows = [review(o, "APPROVED", HEAD, 1) for o in sorted(owners)]
    assert not check(data, rows, owners, HEAD, policy=policy, plan_path=other)[0]
    assert check(data, rows, owners, HEAD, policy=policy, plan_path="./" + PART_PATHS[part])[0] == (
        need <= 3
    )


# --- focused tests for what mutation testing found unchecked -------------------------------------


@pytest.mark.parametrize("bad", ["20261103", "2026-W45-2", "2026W452", "2026-307"])
def test_other_iso_8601_forms_of_a_day_are_refused(bad):
    with pytest.raises(ValueError, match="bad cause_date"):
        bucket_key({"cause_date": bad})


def test_a_review_with_no_user_is_skipped():
    rows = [
        {
            "user": None,
            "state": "APPROVED",
            "commit_id": HEAD,
            "submitted_at": "2026-11-19T01:00:00Z",
        },
        review("kstarev", "APPROVED", HEAD, 2),
    ]
    assert count_approvals(rows, {"kstarev"}, HEAD) == 1


def test_a_later_change_request_listed_first_still_wins():
    rows = [review("kstarev", "CHANGES_REQUESTED", HEAD, 3), review("kstarev", "APPROVED", HEAD, 1)]
    assert count_approvals(rows, {"kstarev"}, HEAD) == 0
    assert count_approvals(list(reversed(rows)), {"kstarev"}, HEAD) == 0


def test_the_split_refuses_a_held_change_with_no_known_hold_kind():
    with pytest.raises(ValueError, match="no known hold kind"):
        split_plan(synthetic([], [change(0, "remove", "", "E1", hold="later")]), BASE)


def test_the_split_refuses_when_main_would_still_need_two_approvals():
    # a removal of a type that always stays in main, on a day over the limit
    day = [change(i, "remove", "2026-11-03", f"E{i}", type_="break_glass") for i in range(5)]
    with pytest.raises(ValueError, match="the main plan needs 2 approvals"):
        split_plan(synthetic(day), BASE)


def test_the_split_refuses_a_held_plan_that_would_need_one_approval():
    # changes held by a bulk import with no block entry next to them
    bulk = [change(0, "remove", "2026-11-03", "E1", hold="bulk")]
    with pytest.raises(ValueError, match="the held plan needs 1 approvals"):
        split_plan(synthetic([], bulk), BASE)
