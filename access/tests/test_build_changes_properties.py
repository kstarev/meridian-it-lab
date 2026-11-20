"""Property-based and focused tests for diff.build_changes (hypothesis; AAC-007).

build_changes turns the builder's changes into the plan's two lists: what is proposed now and
what is held. These tests check what it adds on top of the builder, on made-up people:

- every bulk import that causes any removal or suspension is held, whatever its size, and one
  `block_two_approvers` finding says how many people and removals it holds;
- a change's cause date is the date of its own person's last HR event of the kind that causes
  its type, and nothing else's;
- a reviewed map entry places an account only on a current person.

Gap G5 does not hold: when a caller passes an AI-sourced match, a reviewed map entry that points
at an unknown or superseded employee id makes its account an orphan instead of holding it. Its
tests (`test_g5_*`, at the end) are two strict xfails, and one normal test that shows build_plan
can't reach it today. The source is unchanged.

Written for the mutation pass of build_changes
(docs/evals/2026-11-19-access-safety-mutation-testing.md). The runs are fixed (derandomize) and
keep no example database. No world, no network.
"""

import dataclasses
import json
import re

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from meridian_access import plan as plan_mod
from meridian_access.collect import ActualState
from meridian_access.desired import desired_state
from meridian_access.diff import apply_reviewed_map, build_changes
from meridian_access.match import CONFIDENCE, Match, MatchResult, match_accounts
from meridian_access.models import Account
from meridian_access.policy.load import load_policy
from meridian_dataset.access.model import HrisEvent, HrisRecord

SETTINGS = settings(derandomize=True, max_examples=150, database=None, deadline=None)
POLICY = load_policy()
D = "meridian.example"
SSO = sorted(a.id for a in POLICY.apps.values() if a.sso)
NON_SSO = sorted(a.id for a in POLICY.apps.values() if not a.sso)
REMOVING = ("remove", "suspend")
# The rule written out (dataset/ACCESS_RECIPE.md): the HR event kind behind each type of change.
# A type not listed here has no cause date.
CAUSE = {
    "leaver_access_left": "terminate",
    "contractor_past_end": "terminate",
    "orphaned_account": "terminate",
    "mover_stale_access": "move",
    "leave_of_absence": "leave_start",
}
BLOCK = re.compile(r"^one bulk import ended (\d+) people at once; (\d+) removals or suspensions")


def rec(eid: str, given: str, family: str, **kw: str) -> HrisRecord:
    base = {
        "employee_id": eid,
        "given_name": given,
        "family_name": family,
        "preferred_name": "",
        "work_email": f"{given.lower()}.{family.lower()}@{D}",
        "personal_email": f"{given.lower()}@mail.example",
        "job_title": "Backend Engineer",  # a title the reviewed map knows
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


def ev(eid: str, kind: str, day: int, source: str = "hr", **fields: str) -> HrisEvent:
    return HrisEvent(day, f"2026-11-{day + 1:02d}", eid, kind, tuple(fields.items()), source)


def okta(login: str) -> Account:
    return Account("okta", login, email=login)


def accounts_for(mail: str, kinds: frozenset[str]) -> list[Account]:
    made = {
        "okta": okta(mail),
        "google": Account("google", mail, email=mail),
        "slack": Account("slack", mail, email=mail),
        "sso": Account("okta", mail, email=mail, app_id=SSO[0], role="member"),
        "export": Account(NON_SSO[0], mail, app_id=NON_SSO[0], email=mail),
    }
    return [made[k] for k in sorted(kinds)]


def run(records, accounts, events=(), map_entries=()):
    desired = desired_state(records, events, POLICY)
    actual = ActualState(tuple(accounts), (), None)
    matches = match_accounts(actual.accounts, records, POLICY)
    return build_changes(
        desired, actual, matches, POLICY, records=records, events=events, map_entries=map_entries
    )


def ident(c) -> tuple[str, ...]:
    return (c.type, c.employee_id, c.account, c.system, c.app_id, c.action, c.cause_date)


# --- bulk imports -------------------------------------------------------------------------------

ACCOUNT_KINDS = ("okta", "google", "slack", "sso", "export")


def _leavers(kinds_per_person, bulk: bool, ordinary: bool):
    """Leavers ended by one HR import on day 27, and maybe one ordinary leaver the same day."""
    records, accounts, events = [], [], []
    for i, kinds in enumerate(kinds_per_person):
        r = rec(f"E{i}", "Pat", f"Lee{chr(97 + i)}", status="terminated", end_date="2026-11-28")
        records.append(r)
        accounts += accounts_for(r.work_email, kinds)
        events.append(ev(r.employee_id, "terminate", 27, "bulk_import" if bulk else "hr"))
    if ordinary:
        r = rec("E9", "Ora", "Dinary", status="terminated", end_date="2026-11-28")
        records.append(r)
        accounts.append(okta(r.work_email))
        events.append(ev(r.employee_id, "terminate", 27, "hr"))
    return records, accounts, events


@SETTINGS
@given(
    st.lists(st.frozensets(st.sampled_from(ACCOUNT_KINDS)), min_size=1, max_size=4),
    st.booleans(),
)
@example([frozenset({"okta"})], False)  # exactly one suspension
@example([frozenset({"okta"})], True)
@example([frozenset({"export"}), frozenset()], True)  # a flag only: nothing removed
def test_a_bulk_import_holds_what_the_same_leavers_would_lose_and_says_how_much(kinds, ordinary):
    records, accounts, plain_events = _leavers(kinds, bulk=False, ordinary=ordinary)
    _, _, bulk_events = _leavers(kinds, bulk=True, ordinary=ordinary)
    plain = run(records, accounts, plain_events)
    bulk = run(records, accounts, bulk_events)
    assert plain.held == ()
    theirs = [c for c in plain.changes if c.employee_id != "E9"]
    others = [c for c in plain.changes if c.employee_id == "E9"]
    removals = sum(c.action in REMOVING for c in theirs)
    blocks = [c for c in bulk.changes if c.type == "hris_mass_termination"]
    if removals:
        # every change of the imported people waits for the held plan, none is proposed now
        assert sorted(map(ident, bulk.held)) == sorted(map(ident, theirs))
        assert {c.hold for c in bulk.held} == {"bulk"}
        assert [ident(c) for c in bulk.changes if c not in blocks] == [ident(c) for c in others]
        assert len(blocks) == 1
        block = blocks[0]
        assert (block.action, block.system, block.employee_id, block.account) == (
            "block_two_approvers",
            "hris",
            "",
            "",
        )
        said = BLOCK.match(block.reason)
        assert said, block.reason
        assert (int(said[1]), int(said[2])) == (len(kinds), removals)
    else:
        # nothing to remove or suspend: nothing is held and no block is raised
        assert bulk.held == ()
        assert blocks == []
        assert [ident(c) for c in bulk.changes] == [ident(c) for c in plain.changes]


def test_a_bulk_import_that_suspends_one_account_is_held():
    r = rec("E1", "Pat", "Lee", status="terminated", end_date="2026-11-28")
    result = run([r], [okta(r.work_email)], [ev("E1", "terminate", 27, "bulk_import")])
    assert [(c.type, c.action) for c in result.changes] == [
        ("hris_mass_termination", "block_two_approvers")
    ]
    assert [(c.system, c.action, c.hold) for c in result.held] == [("okta", "suspend", "bulk")]
    assert result.changes[0].reason.startswith(
        "one bulk import ended 1 people at once; 1 removals or suspensions held"
    )


# --- cause dates --------------------------------------------------------------------------------

# A leaver, an active person with a stray app (drift), a mover, a person on leave and an orphan.
LEAVER = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-03")
ACTIVE = rec("E2", "Bo", "Reed")
MOVER = rec("E3", "Cy", "Moss", department="Sales")
ON_LEAVE = rec("E4", "Di", "Fox", status="on_leave", leave_start="2026-11-05")
PEOPLE = [LEAVER, ACTIVE, MOVER, ON_LEAVE]
WORLD = [
    okta(LEAVER.work_email),
    okta(f"ghost@{D}"),
    okta(ACTIVE.work_email),
    Account("okta", ACTIVE.work_email, email=ACTIVE.work_email, app_id="zzz-unlisted-app"),
    Account("okta", MOVER.work_email, email=MOVER.work_email, app_id="github", role="member"),
    okta(ON_LEAVE.work_email),
]
# Events that name nobody, or that have no kind: they are no change's cause.
STRAY = (("", "terminate"), ("", "move"), ("", "leave_start"), ("E1", ""), ("E2", ""), ("E3", ""))


def expected_date(c, events) -> str:
    kind = CAUSE.get(c.type)
    if not kind or not c.employee_id:
        return ""
    days = [e for e in events if e.employee_id == c.employee_id and e.kind == kind]
    return max(days, key=lambda e: e.day).date if days else ""


@SETTINGS
@given(
    st.lists(st.integers(1, 28), min_size=1, max_size=3, unique=True),  # the leaver's terminates
    st.lists(st.integers(1, 28), min_size=1, max_size=3, unique=True),  # the mover's moves
    st.integers(1, 28),  # the leave start
    st.lists(st.tuples(st.sampled_from(STRAY), st.integers(0, 29)), max_size=6),
    st.randoms(use_true_random=False),
)
def test_a_cause_date_comes_only_from_the_person_s_own_event_of_the_cause_kind(
    ends, moves, leave, stray, rnd
):
    events = [ev("E1", "terminate", d) for d in ends]
    events += [ev("E3", "move", d, department="Sales") for d in moves]
    events.append(ev("E4", "leave_start", leave))
    events += [ev(eid, kind, d) for (eid, kind), d in stray]
    rnd.shuffle(events)  # input order never matters
    result = run(PEOPLE, WORLD, events)
    types = {c.type for c in result.changes}
    assert {"leaver_access_left", "orphaned_account", "drift", "mover_stale_access"} <= types
    for c in [*result.changes, *result.held]:
        assert c.cause_date == expected_date(c, events), c


def test_an_orphan_and_drift_get_no_cause_date_from_an_event_that_names_nobody_or_nothing():
    events = [ev("E1", "terminate", 9), ev("", "terminate", 20), ev("E2", "", 23)]
    result = run(PEOPLE, WORLD, events)
    dates = {(c.type, c.employee_id): c.cause_date for c in result.changes}
    assert dates[("leaver_access_left", "E1")] == "2026-11-10"
    assert dates[("orphaned_account", "")] == ""
    assert dates[("drift", "E2")] == ""


# --- reviewed map entries -----------------------------------------------------------------------


def _entry(account: Account, value: str) -> dict:
    return {"kind": "account", "key": "/".join(account.key), "value": value, "reviewed": True}


def test_a_reviewed_entry_places_an_account_on_the_current_person_it_names():
    leaver = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-11-10")
    mystery = Account(NON_SSO[0], "zz9", app_id=NON_SSO[0])
    accounts = [okta(leaver.work_email), mystery]
    events = [ev("E1", "terminate", 9)]
    unplaced = run([leaver], accounts, events)
    assert [(c.type, c.employee_id) for c in unplaced.changes if c.account == "zz9"] == [
        ("orphaned_account", "")
    ]
    placed = run([leaver], accounts, events, map_entries=[_entry(mystery, "E1")])
    assert [(c.type, c.employee_id) for c in placed.changes if c.account == "zz9"] == [
        ("leaver_access_left", "E1")
    ]
    assert not [w for w in placed.warnings if "unknown or superseded" in w]


def test_a_reviewed_entry_naming_the_old_record_of_a_rehire_is_not_used():
    new = rec("E2", "Ada", "Stone", start_date="2026-11-03")
    old = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-04-01")  # same address
    mystery = Account(NON_SSO[0], "zz9", app_id=NON_SSO[0])
    accounts = [okta(new.work_email), mystery]
    events = [ev("E2", "rehire", 2, work_email=new.work_email)]
    people = desired_state([old, new], events, POLICY).people
    assert {p.employee_id: p.category for p in people}["E1"] == "superseded"
    to_old = run([old, new], accounts, events, map_entries=[_entry(mystery, "E1")])
    assert not [c for c in to_old.changes if c.account == "zz9"]
    assert any("points at E1, an unknown or superseded employee id" in w for w in to_old.warnings)
    assert any(w.startswith("zz9 ") and "ambiguous owner" in w for w in to_old.warnings)
    to_new = run([old, new], accounts, events, map_entries=[_entry(mystery, "E2")])
    assert [(c.type, c.employee_id) for c in to_new.changes if c.account == "zz9"] == [
        ("rehire", "E2")
    ]


# --- gap G5: a bad reviewed entry under an AI match -----------------------------------------------
#
# apply_reviewed_map does not use a reviewed map entry that points at an unknown or superseded
# employee id: it warns and holds the account (ambiguous), never orphans it. But when the caller
# also passes an AI-sourced match for that account, the match is dropped (nothing usable backs it)
# and the account is listed as unmatched: an orphan, which for an Okta user is a removal.
# build_plan can't reach this today: it passes match_accounts' result straight on, and
# match_accounts makes no AI match (the last test). Strict xfails; the source is unchanged.

G5_REASON = (
    "gap G5: with an AI-sourced match, a reviewed map entry that points at an unknown or "
    "superseded employee id makes the account an orphan instead of holding it"
)
G5_NEW = rec("E2", "Ada", "Stone", start_date="2026-11-03")
G5_OLD = rec("E1", "Ada", "Stone", status="terminated", end_date="2026-04-01")  # superseded
G5_RECORDS = [G5_OLD, G5_NEW]
G5_EVENTS = [ev("E2", "rehire", 2, work_email=G5_NEW.work_email)]
GHOST = okta(f"ghost@{D}")  # an Okta user the rules place on nobody
HELD = f"{GHOST.account} (okta): ambiguous owner, needs a map entry"


def _reviewed_ai_entry(value: str) -> dict:
    """An AI suggestion for GHOST that a person set to reviewed, with the owner `value`."""
    return {**_entry(GHOST, value), "source": "ai", "model": "fake-model"}


@pytest.mark.xfail(strict=True, reason=G5_REASON)
@pytest.mark.parametrize("rule", ["ai", "ai_suggestion"])
def test_g5_a_bad_reviewed_entry_holds_an_account_an_ai_match_placed(rule):
    entries = [_reviewed_ai_entry("E999")]
    # with no AI match, the bad entry holds the account
    plain, _, _ = apply_reviewed_map(MatchResult({}, [GHOST.key], []), entries, {"E2"})
    assert (plain.unmatched, plain.ambiguous) == ([], [GHOST.key])
    ai = MatchResult({GHOST.key: Match("E2", rule, 0.5)}, [], [])
    result, _, warnings = apply_reviewed_map(ai, entries, {"E2"})
    assert len(warnings) == 1 and "points at E999, an unknown or superseded" in warnings[0]
    assert GHOST.key not in result.matches  # nothing usable backs the AI match
    assert (result.unmatched, result.ambiguous) == ([], [GHOST.key])  # held, not an orphan


@pytest.mark.xfail(strict=True, reason=G5_REASON)
@pytest.mark.parametrize("bad", ["E999", "E1"], ids=["unknown", "superseded"])
def test_g5_build_changes_does_not_remove_that_okta_account(bad):
    desired = desired_state(G5_RECORDS, G5_EVENTS, POLICY)
    actual = ActualState((okta(G5_NEW.work_email), GHOST), (), None)
    rules = match_accounts(actual.accounts, G5_RECORDS, POLICY)
    assert GHOST.key in rules.unmatched
    # the AI's owner on top of the rules, as ai/final.py combines them for scoring
    with_ai = MatchResult(
        {**rules.matches, GHOST.key: Match("E2", "ai", 0.5)},
        [k for k in rules.unmatched if k != GHOST.key],
        list(rules.ambiguous),
    )
    result = build_changes(
        desired,
        actual,
        with_ai,
        POLICY,
        records=G5_RECORDS,
        events=G5_EVENTS,
        map_entries=[_reviewed_ai_entry(bad)],
    )
    assert any(f"points at {bad}, an unknown or superseded" in w for w in result.warnings)
    ghost = [
        (c.type, c.action) for c in (*result.changes, *result.held) if c.account == GHOST.account
    ]
    assert ghost == []  # today: [("orphaned_account", "remove")]
    assert HELD in result.warnings


@pytest.mark.parametrize("bad", ["E999", "E1"], ids=["unknown", "superseded"])
def test_g5_build_plan_cannot_reach_it_today(bad, tmp_path, monkeypatch):
    """build_plan hands build_changes what match_accounts gives, and match_accounts makes no AI
    match (its rules are CONFIDENCE's, none from an AI). So in a plan the same reviewed entry
    holds the account. If an AI match ever reaches build_plan, this fails and G5 is live."""
    assert [rule for rule in CONFIDENCE if rule.startswith("ai")] == []
    hris = {
        "records": [dataclasses.asdict(r) for r in G5_RECORDS],
        "events": [
            {
                "day": e.day,
                "date": e.date,
                "employee_id": e.employee_id,
                "kind": e.kind,
                "fields": dict(e.fields),
                "source": e.source,
            }
            for e in G5_EVENTS
        ],
    }
    (tmp_path / "hris.json").write_text(json.dumps(hris))
    passed: list[MatchResult] = []

    def spy(desired, actual, matches, policy, **kw):
        passed.append(matches)
        return build_changes(desired, actual, matches, policy, **kw)

    monkeypatch.setattr(plan_mod, "build_changes", spy)
    state = ActualState((okta(G5_NEW.work_email), GHOST), (), None)
    built = plan_mod.build_plan(
        tmp_path, POLICY, state=state, map_entries=[_reviewed_ai_entry(bad)]
    )
    (matches,) = passed
    assert [m.rule for m in matches.matches.values() if m.rule.startswith("ai")] == []
    assert GHOST.key in matches.unmatched
    rows = [
        c for c in (*built.data["changes"], *built.data["held"]) if c["account"] == GHOST.account
    ]
    assert rows == []
    assert HELD in built.data["warnings"]
    assert any(f"points at {bad}, an unknown or superseded" in w for w in built.data["warnings"])
