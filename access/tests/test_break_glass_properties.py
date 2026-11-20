"""Property-based tests for the break-glass checks (hypothesis; AAC-007).

The rule (policy/break_glass.yaml, AAC-018): a name is protected if it is listed or fully
matches a pattern, with spaces around it ignored and case folded as matching folds it
(str.casefold), in either of its forms: a bare username `name` is also `name@meridian.example`,
and a company address is also its bare username. Every name a protected account goes by (its
login, its email, a Google alias, wherever a record carries them) is protected too, in both
forms. The policy checks one name (`Policy.break_glass_kind`); `break_glass.py` works out every
name of every protected account, and matching, the diff's last word and apply's final guard all
ask it. The rule is checked here on made-up names, and each of the three places end to end (the
`test_g2_*` tests). The property that no write reaches a protected account is in
test_apply_properties.py.

Gap G2 (the owner's ruling, 2026-11-19: protection covers every name of a protected account,
aliases included) was pinned by the `test_g2_*` tests as strict xfails and is fixed by AAC-018.
Gap G7 (found in review, 2026-11-20: a Slack record the person can edit spreads that protection to
their own HR address) is pinned by the `test_g7_*` tests as strict xfails.

The runs are fixed (derandomize) and keep no example database.
"""

import copy
import dataclasses
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.apply import apply_plan, issue_write_tokens
from meridian_access.break_glass import break_glass, name_forms, policy_kind
from meridian_access.collect import ActualState, Mocks, actual_state
from meridian_access.desired import desired_state
from meridian_access.diff import build_changes
from meridian_access.match import Match, MatchResult, match_accounts
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock
from meridian_access.models import Account
from meridian_access.plan import Plan
from meridian_access.policy.load import load_policy
from meridian_dataset.access.model import HrisEvent, HrisRecord

SETTINGS = settings(derandomize=True, max_examples=200, database=None, deadline=None)
BASE = load_policy()
D = "meridian.example"
LISTED = (f"breakglass-01@{D}", f"Ops-Root@{D}", f"vault.admin@{D}")
POLICY = dataclasses.replace(
    BASE,
    break_glass_accounts=frozenset(LISTED),
    break_glass_patterns=(re.compile(r"^breakglass-[0-9]+@meridian\.example$"),),
)
PATTERNED = (f"breakglass-7@{D}", f"breakglass-123@{D}")
NOT_PROTECTED = (
    f"breakglass-x@{D}",
    f"breakglass-01@{D}.evil",
    f"xbreakglass-01@{D}",
    "breakglass-01@mail.example",
    f"ops-root@{D}x",
    "ops-root2",
    f"ana@{D}",
    "",
    "   ",
)


def _case(text: str, flips: list[bool]) -> str:
    return "".join(
        c.upper() if f else c.lower()
        for c, f in zip(text, flips + [False] * len(text), strict=False)
    )


@st.composite
def spellings(draw, names, bare=True):
    """A protected name in random case, maybe bare, with spaces or tabs around it."""
    name = draw(st.sampled_from(names))
    if bare and draw(st.booleans()):
        name = name.split("@")[0]
    name = _case(name, draw(st.lists(st.booleans(), max_size=len(name))))
    pad = st.sampled_from(("", " ", "\t", "  "))
    return draw(pad) + name + draw(pad)


def kind_by_rule(value: str) -> str | None:
    """The rule written out: as written first, then the other form (a bare name gets the
    domain, a company address loses it); listed beats pattern within one form."""
    name = value.strip().casefold()
    other = f"{name}@{D}" if "@" not in name else name.removesuffix(f"@{D}")
    for candidate in (name, other):
        if not candidate:
            continue
        if candidate in {a.casefold() for a in LISTED}:
            return "listed"
        if re.fullmatch(r"breakglass-[0-9]+@meridian\.example", candidate):
            return "pattern"
    return None


def _kind(policy, a: Account) -> str | None:
    """Matching's word on one record, alone in the actual state."""
    return break_glass(policy, [a]).account_kind(a)


# --- the policy -----------------------------------------------------------------------------


@SETTINGS
@given(spellings(LISTED + PATTERNED))
def test_the_policy_knows_every_spelling_of_a_protected_address(name):
    full = name if "@" in name else f"{name.strip()}@{D}"
    want = kind_by_rule(full)
    assert want is not None
    assert POLICY.break_glass_kind(full) == want
    assert POLICY.is_break_glass(full) is True


@SETTINGS
@given(st.sampled_from(NOT_PROTECTED))
def test_a_near_miss_is_not_protected(name):
    assert POLICY.break_glass_kind(name) is None
    assert POLICY.is_break_glass(name) is False


def test_listed_beats_pattern():
    assert POLICY.break_glass_kind(f"BREAKGLASS-01@{D}") == "listed"
    assert POLICY.break_glass_kind(f"breakglass-02@{D}") == "pattern"


# --- matching ------------------------------------------------------------------------------


@SETTINGS
@given(
    spellings(LISTED + PATTERNED),
    st.sampled_from(("account", "email", "alias")),
    st.sampled_from(("okta", "google", "slack", "github")),
)
def test_matching_marks_a_protected_name_wherever_the_account_carries_it(name, where, system):
    other = f"ana@{D}"
    fields = {"account": other, "email": other, "aliases": (other,)}
    if where == "alias":
        fields["aliases"] = (other, name)
    else:
        fields[where] = name
    a = Account(system=system, **fields)
    assert _kind(POLICY, a) == kind_by_rule(name)


@SETTINGS
@given(st.lists(st.sampled_from(NOT_PROTECTED), max_size=4))
def test_matching_leaves_an_account_with_no_protected_name_alone(names):
    a = Account(
        system="google",
        account=names[0] if names else "",
        email=names[1] if len(names) > 1 else "",
        aliases=tuple(names[2:]),
    )
    assert _kind(POLICY, a) is None


# --- every name of a protected account (what the diff's last word and apply's guard ask) ---------

# A protected Okta user whose login is not protected by itself: only its listed email makes it
# so, and both guards must then protect the login too. And a Google user protected by an alias.
STATE = ActualState(
    (
        Account(system="okta", account="root-login", email=f"vault.admin@{D}"),
        Account(system="okta", account="root-login", app_id="github", email=""),
        Account(
            system="google", account=f"ops@{D}", email=f"ops@{D}", aliases=(f"breakglass-9@{D}",)
        ),
        Account(system="slack", account="ana", email=f"ana@{D}"),
        Account(system="okta", account=f"ana@{D}", email=f"ana@{D}"),
    ),
    (),
    None,
)
ALSO_PROTECTED = ("root-login", f"root-login@{D}", f"ops@{D}")
PROTECTED = break_glass(POLICY, STATE.accounts)


@SETTINGS
@given(st.one_of(spellings(LISTED + PATTERNED), spellings(ALSO_PROTECTED)))
def test_both_guards_stop_every_spelling_of_every_protected_name(name):
    # names protected only through another name of their account are protected in any case,
    # with spaces around, and in both forms: `ops` is `ops@meridian.example` (gap G2)
    assert PROTECTED.protects(name)


@SETTINGS
@given(st.sampled_from((*NOT_PROTECTED, "ana", f"ANA@{D}", "root-login2", f"ops2@{D}")))
def test_neither_guard_stops_an_ordinary_account(name):
    assert not PROTECTED.protects(name)


def test_the_guards_collect_every_name_of_a_protected_account_and_only_those():
    listed = {"root-login", f"root-login@{D}", f"vault.admin@{D}", "vault.admin"}
    pattern = {f"ops@{D}", "ops", f"breakglass-9@{D}", "breakglass-9"}
    assert dict(PROTECTED.names) == dict.fromkeys(listed, "listed") | dict.fromkeys(
        pattern, "pattern"
    )


@SETTINGS
@given(spellings(LISTED + PATTERNED))
def test_the_policy_check_takes_a_bare_name_or_a_full_address(name):
    assert policy_kind(POLICY, name) == kind_by_rule(name) is not None
    assert policy_kind(POLICY, f"x{name.strip()}") is None


@SETTINGS
@given(
    st.text(alphabet="aB.-_@ \t\u017f", max_size=10),
    st.sampled_from(("", f"@{D}", f"@{D.upper()}", "@mail.example")),
)
def test_a_name_has_at_most_two_forms_and_each_form_gives_back_the_same_pair(local, domain):
    forms = name_forms(local + domain)
    assert len(forms) <= 2
    for form in forms:
        assert form and form == form.strip().casefold()
        assert set(name_forms(form)) == set(forms)


def test_a_record_takes_the_kind_of_its_account():
    # its own name only matches the pattern, but another name of its account is listed
    okta = Account(system="okta", account=f"breakglass-77@{D}", email=f"breakglass-77@{D}")
    google = Account(
        system="google",
        account=f"breakglass-77@{D}",
        email=f"breakglass-77@{D}",
        aliases=(f"breakglass-01@{D}",),
    )
    both = break_glass(POLICY, [okta, google])
    assert both.account_kind(okta) == both.account_kind(google) == "listed"
    assert _kind(POLICY, okta) == "pattern"


def test_a_record_outside_the_state_is_still_checked_against_the_policy():
    empty = break_glass(POLICY, [])
    assert empty.names == {}
    assert empty.account_kind(Account(system="slack", account="breakglass-01")) == "listed"
    assert empty.account_kind(Account(system="slack", account="breakglass-5")) == "pattern"
    assert empty.account_kind(Account(system="slack", account="ops")) is None
    assert empty.protects(" BREAKGLASS-01 ") and not empty.protects("ops")


# --- focused tests for what mutation testing found unchecked -------------------------------------


def test_a_bare_name_the_policy_lists_is_protected_in_both_forms():
    bare = dataclasses.replace(POLICY, break_glass_accounts=frozenset({"rootbare"}))
    for a in (
        Account(system="slack", account="rootbare"),
        Account(system="github", account=" RootBare "),
        Account(system="google", account=f"x@{D}", email=f"x@{D}", aliases=("rootbare",)),
        Account(system="slack", account=f"rootbare@{D}"),  # its company address (gap G2)
    ):
        assert _kind(bare, a) == "listed"
    assert _kind(bare, Account(system="slack", account="rootbare@mail.example")) is None


# --- gap G2: every name of a protected account ----------------------------------------------------
#
# The owner's ruling (2026-11-19): break-glass protection covers every name of a protected
# account, aliases included. An account is protected when one of its names is, and then all its
# names are: its login, its email and each Google alias, wherever a record carries them, each also
# as a bare username (`ops` for `ops@meridian.example`) and as a company address
# (`root-login@meridian.example` for the login `root-login`), in any case matching treats as the
# same (it compares with str.casefold, so `OPS` and `op` plus a long s are `ops`), with spaces
# around. Before AAC-018 a bare username was protected only when the policy listed it or a pattern
# matched it, the policy compared with str.lower, and matching looked only at the names one record
# carried. These were strict xfails until the fix.

LONG_S = "\u017f"  # the long s: str.casefold() makes it "s", str.lower() keeps it

# A Google user protected only through its listed alias, with a second alias; an Okta user
# protected only through its listed email.
G2_GOOGLE = Account(
    system="google",
    account=f"ops@{D}",
    email=f"ops@{D}",
    aliases=(f"breakglass-01@{D}", f"it-ops@{D}"),
)
G2_OKTA = Account(system="okta", account="root-login", email=f"vault.admin@{D}")
G2_NAMES = (
    f"ops@{D}",
    "ops",
    f"breakglass-01@{D}",
    "breakglass-01",
    f"it-ops@{D}",
    "it-ops",
    "root-login",
    f"root-login@{D}",
    f"vault.admin@{D}",
    "vault.admin",
)


@st.composite
def other_names(draw):
    """A name of a protected account, in a case matching treats as the same, with spaces."""
    name = draw(st.sampled_from(G2_NAMES))
    name = _case(name, draw(st.lists(st.booleans(), max_size=len(name))))
    if draw(st.booleans()):
        name = name.replace("s", LONG_S)
    pad = st.sampled_from(("", " ", "\t"))
    return draw(pad) + name + draw(pad)


def _record(system: str, name: str) -> Account:
    """Another record that carries `name`: an Okta user, a Slack user or an export row."""
    email = name.strip() if "@" in name else ""
    app_id = "github" if system == "github" else ""
    return Account(system=system, account=name, email=email, app_id=app_id)


@pytest.mark.parametrize(
    "name", [f"Op{LONG_S}-Root@{D}", f"op{LONG_S}-root@{D}", f" OP{LONG_S}-ROOT@{D} "]
)
def test_g2_the_policy_takes_every_case_variant_matching_treats_as_the_same(name):
    assert name.strip().casefold() == f"ops-root@{D}"
    assert POLICY.break_glass_kind(name) == "listed"


@pytest.mark.parametrize("bare", ["rootbare", "RootBare"])
def test_g2_a_bare_name_the_policy_lists_protects_its_company_address(bare):
    policy = dataclasses.replace(POLICY, break_glass_accounts=frozenset({bare}))
    a = Account(system="slack", account=f"rootbare@{D}", email=f"rootbare@{D}")
    m = match_accounts([a], [], policy).matches.get(a.key)
    assert m is not None and m.rule == "break_glass"


@SETTINGS
@given(other_names(), st.sampled_from(("okta", "slack", "github")))
def test_g2_matching_marks_every_record_of_a_protected_account(name, system):
    other = _record(system, name)
    m = match_accounts([G2_GOOGLE, G2_OKTA, other], [], POLICY).matches.get(other.key)
    assert m is not None and (m.employee_id, m.rule) == ("", "break_glass")


@SETTINGS
@given(other_names(), st.sampled_from(("okta", "slack", "github")))
def test_g2_the_diff_drops_every_change_that_names_a_protected_account(name, system):
    other = _record(system, name)
    actual = ActualState((G2_GOOGLE, G2_OKTA, other), (), None)
    marked = Match("", "break_glass", 1.0, "listed")
    # matching missed `other` (an orphan, so a removal or a flag): the last word must drop it
    matches = MatchResult({G2_GOOGLE.key: marked, G2_OKTA.key: marked}, [other.key], [])
    result = build_changes(desired_state([], [], POLICY), actual, matches, POLICY)
    assert [c for c in (*result.changes, *result.held) if c.type != "break_glass"] == []


def _g2_mocks(system: str, name: str) -> Mocks:
    """The two protected accounts, and one more user called `name` in Okta or Slack."""
    okta_users = [("00u1", "root-login", f"vault.admin@{D}")]
    if system == "okta":
        okta_users.append(("00u2", name, f"{name}@mail.example"))
    okta = {
        "users": [
            {
                "id": uid,
                "status": "ACTIVE",
                "created": "2025-01-01T00:00:00.000Z",
                "lastLogin": None,
                "profile": {"login": login, "email": email, "firstName": "", "lastName": ""},
            }
            for uid, login, email in okta_users
        ],
        "apps": [],
        "assignments": [],
        "system_log": [],
    }
    google = {
        "users": [
            {
                "id": "g1",
                "primaryEmail": G2_GOOGLE.account,
                "name": {"givenName": "", "familyName": "", "fullName": ""},
                "suspended": False,
                "orgUnitPath": "/",
                "aliases": list(G2_GOOGLE.aliases),
                "creationTime": "",
                "lastLoginTime": "",
            }
        ]
    }
    slack_users = [("U1", name, "team@mail.example")] if system == "slack" else []
    slack = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": len(slack_users),
        "Resources": [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "id": sid,
                "userName": user,
                "displayName": user,
                "emails": [{"value": email, "primary": True}],
                "active": True,
            }
            for sid, user, email in slack_users
        ],
    }
    return Mocks(OktaMock(okta), GoogleMock(google), SlackMock(slack))


@pytest.mark.parametrize(
    ("system", "name"),
    [
        ("slack", "ops"),
        ("slack", "OPS"),
        ("slack", f"op{LONG_S}"),
        ("slack", "it-ops"),
        ("okta", f"root-login@{D}"),
    ],
)
def test_g2_apply_drops_every_change_that_names_a_protected_account(system, name):
    mocks = _g2_mocks(system, name)
    row = {
        "type": "orphaned_account",
        "employee_id": "",
        "account": name,
        "system": system,
        "app_id": "",
        "app_name": "",
        "action": "remove",
        "reason": "test",
        "evidence": [],
        "cause_date": "",
        "hold": "",
    }
    plan = Plan(
        {
            "version": 2,
            "part": "main",
            "as_of": "2026-12-01",
            "actual_state_sha256": actual_state(mocks, POLICY, sleep=lambda s: None).digest(),
            "changes": [row],
            "held": [],
            "warnings": [],
            "manual_tasks": [],
            "approvals_required": 1,
        }
    )
    before = (copy.deepcopy(mocks.okta.data), copy.deepcopy(mocks.slack.data))
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=2,
        policy=POLICY,
        write_token=issue_write_tokens(mocks),
        sleep=lambda s: None,
        rebuild=lambda state: plan,
    )
    assert not result.refused_reason
    assert result.dropped_break_glass == [row]
    assert result.applied == []
    assert (mocks.okta.data, mocks.slack.data) == before


# --- gap G7: protection spread through a record the person can edit -----------------------------
#
# Found in review (2026-11-20, finding F2). Since AAC-018 every record that shares a name is one
# account, whoever controls the record. A person can edit their own Slack profile (its email, and
# the userName a Slack user can plant). So a leaver who sets their Slack email to a break-glass
# address, or their Slack userName to a break-glass name, links their own HR work address to it:
# matching marks their Okta and Google users break-glass, the diff's last word and apply's guard
# drop their suspension, and each never_touch row says "listed in the break-glass policy", which
# their address is not. They are never offboarded. These are strict xfails until the owner rules.

G7_BG = f"breakglass-01@{D}"  # listed in POLICY
G7_HR = f"ana.smith@{D}"  # the leaver's HR work address
G7_SLACK = {
    "slack email": Account(system="slack", account="ana.smith", email=G7_BG),
    "slack userName": Account(system="slack", account="breakglass-01", email=G7_HR),
}
G7_OKTA = Account(system="okta", account=G7_HR, email=G7_HR)
G7_GOOGLE = Account(system="google", account=G7_HR, email=G7_HR)
G7_BG_OKTA = Account(system="okta", account=G7_BG, email=G7_BG)  # the real break-glass user
G7_GAP = (
    "gap G7: a Slack record the person can edit spreads break-glass protection to their HR address"
)
g7 = pytest.mark.xfail(strict=True, reason=G7_GAP)
g7_slack = pytest.mark.parametrize("slack", G7_SLACK.values(), ids=G7_SLACK)


def _g7_leaver() -> HrisRecord:
    return HrisRecord(
        employee_id="E1",
        given_name="Ana",
        family_name="Smith",
        preferred_name="",
        work_email=G7_HR,
        personal_email="ana@mail.example",
        job_title="Backend Engineer",
        department="Engineering",
        team="Core",
        manager_id="",
        country="DE",
        hub="x",
        employment_type="employee",
        status="terminated",
        start_date="2025-01-01",
        end_date="2026-11-10",
        leave_start="",
        leave_end="",
    )


G7_EVENTS = (HrisEvent(9, "2026-11-10", "E1", "terminate", (), "hr"),)


def _g7_state(slack: Account) -> ActualState:
    return ActualState((G7_OKTA, G7_GOOGLE, slack, G7_BG_OKTA), (), None)


def _g7_changes(slack: Account, matches: MatchResult | None = None):
    leaver = _g7_leaver()
    actual = _g7_state(slack)
    if matches is None:
        matches = match_accounts(actual.accounts, [leaver], POLICY)
    desired = desired_state([leaver], G7_EVENTS, POLICY)
    return build_changes(desired, actual, matches, POLICY, records=[leaver], events=G7_EVENTS)


def _suspended(result) -> set[tuple[str, str]]:
    return {(c.system, c.account) for c in (*result.changes, *result.held) if c.action == "suspend"}


@g7
@g7_slack
def test_g7_matching_places_a_leavers_own_records_on_them_whatever_their_slack_record_says(slack):
    result = match_accounts(_g7_state(slack).accounts, [_g7_leaver()], POLICY)
    for a in (G7_OKTA, G7_GOOGLE):
        m = result.matches.get(a.key)
        assert m is not None and (m.employee_id, m.rule) == ("E1", "exact_email")


@g7
@g7_slack
def test_g7_a_leaver_is_offboarded_whatever_their_slack_record_says(slack):
    result = _g7_changes(slack)
    assert {("okta", G7_HR), ("google", G7_HR)} <= _suspended(result)
    assert [c for c in result.changes if c.action == "never_touch" and c.account == G7_HR] == []


@g7
@g7_slack
def test_g7_the_diffs_last_word_keeps_a_leavers_suspension(slack):
    # matching placed the leaver's records on them: the last word must not drop the suspension
    owner = Match("E1", "exact_email", 1.0)
    marked = Match("", "break_glass", 1.0, "listed")
    matches = MatchResult(
        {G7_OKTA.key: owner, G7_GOOGLE.key: owner, slack.key: marked, G7_BG_OKTA.key: marked},
        [],
        [],
    )
    result = _g7_changes(slack, matches)
    assert {("okta", G7_HR), ("google", G7_HR)} <= _suspended(result)
    assert not [w for w in result.warnings if "dropped: break-glass account" in w]


def _g7_mocks(slack: Account) -> Mocks:
    okta = {
        "users": [
            {
                "id": uid,
                "status": "ACTIVE",
                "created": "2025-01-01T00:00:00.000Z",
                "lastLogin": None,
                "profile": {"login": login, "email": login, "firstName": "", "lastName": ""},
            }
            for uid, login in (("00u1", G7_HR), ("00u2", G7_BG))
        ],
        "apps": [],
        "assignments": [],
        "system_log": [],
    }
    google = {
        "users": [
            {
                "id": "g1",
                "primaryEmail": G7_HR,
                "name": {"givenName": "Ana", "familyName": "Smith", "fullName": "Ana Smith"},
                "suspended": False,
                "orgUnitPath": "/",
                "aliases": [],
                "creationTime": "",
                "lastLoginTime": "",
            }
        ]
    }
    slack_data = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "Resources": [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "id": "U1",
                "userName": slack.account,
                "displayName": "Ana Smith",
                "emails": [{"value": slack.email, "primary": True}],
                "active": True,
            }
        ],
    }
    return Mocks(OktaMock(okta), GoogleMock(google), SlackMock(slack_data))


@g7
@g7_slack
def test_g7_apply_suspends_a_leaver_whatever_their_slack_record_says(slack):
    mocks = _g7_mocks(slack)
    row = {
        "type": "leaver_access_left",
        "employee_id": "E1",
        "account": G7_HR,
        "system": "okta",
        "app_id": "",
        "app_name": "",
        "action": "suspend",
        "reason": "test",
        "evidence": [],
        "cause_date": "2026-11-10",
        "hold": "",
    }
    plan = Plan(
        {
            "version": 2,
            "part": "main",
            "as_of": "2026-12-01",
            "actual_state_sha256": actual_state(mocks, POLICY, sleep=lambda s: None).digest(),
            "changes": [row],
            "held": [],
            "warnings": [],
            "manual_tasks": [],
            "approvals_required": 1,
        }
    )
    result = apply_plan(
        plan,
        mocks,
        mode="mock-live",
        approvals=2,
        policy=POLICY,
        write_token=issue_write_tokens(mocks),
        sleep=lambda s: None,
        rebuild=lambda state: plan,
    )
    assert not result.refused_reason
    assert result.dropped_break_glass == []
    assert [{k: v for k, v in c.items() if k != "call"} for c in result.applied] == [row]
    status = {u["profile"]["login"]: u["status"] for u in mocks.okta.data["users"]}
    assert status == {G7_HR: "SUSPENDED", G7_BG: "ACTIVE"}


@g7
@g7_slack
def test_g7_break_glass_evidence_says_listed_only_for_a_name_the_policy_lists(slack):
    rows = [c for c in _g7_changes(slack).changes if c.action == "never_touch"]
    assert rows  # the real break-glass user, at least
    for c in rows:
        if "listed in the break-glass policy" in c.evidence:
            assert policy_kind(POLICY, c.account) == "listed", (c.system, c.account, c.evidence)
