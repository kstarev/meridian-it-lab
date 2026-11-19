"""Property-based tests for the break-glass checks (hypothesis; AAC-007).

The rule (policy/break_glass.yaml): an account is protected if it is listed or fully matches a
pattern, case-insensitive, with spaces around it ignored, and also as a bare username
(`name` stands for `name@meridian.example`). Every name a protected account goes by (its login,
its email, a Google alias) is protected too. Four places check it: the policy itself, matching
(`match._break_glass`), the diff's last word (`diff._touches_break_glass`) and apply's final
guard (`apply._touches_break_glass`). Each is checked here against the same rule, on made-up
names. The end-to-end check (no write reaches a protected account) is in
test_apply_properties.py.

Gap G2 (the owner's ruling, 2026-11-19: protection covers every name of a protected account,
aliases included) does not hold yet: `ops` is not protected when `ops@meridian.example` is
protected through its alias, matching marks only the record that carries the protected name, and
the policy compares with str.lower where matching uses str.casefold. Its tests (`test_g2_*`, at
the end) are strict xfails; the source is unchanged.

The runs are fixed (derandomize) and keep no example database.
"""

import copy
import dataclasses
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access import apply as apply_mod
from meridian_access import diff as diff_mod
from meridian_access.apply import apply_plan, issue_write_tokens
from meridian_access.collect import ActualState, Mocks, actual_state
from meridian_access.desired import desired_state
from meridian_access.diff import Change, build_changes
from meridian_access.match import Match, MatchResult, _break_glass, match_accounts
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock
from meridian_access.models import Account
from meridian_access.plan import Plan
from meridian_access.policy.load import load_policy

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
    """The rule written out: listed beats pattern; a bare name gets the domain."""
    name = value.strip().lower()
    for candidate in (name, f"{name}@{D}" if name and "@" not in name else None):
        if candidate is None:
            continue
        if candidate in {a.lower() for a in LISTED}:
            return "listed"
        if re.fullmatch(r"breakglass-[0-9]+@meridian\.example", candidate):
            return "pattern"
    return None


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
    assert _break_glass(POLICY, a) == kind_by_rule(name)


@SETTINGS
@given(st.lists(st.sampled_from(NOT_PROTECTED), max_size=4))
def test_matching_leaves_an_account_with_no_protected_name_alone(names):
    a = Account(
        system="google",
        account=names[0] if names else "",
        email=names[1] if len(names) > 1 else "",
        aliases=tuple(names[2:]),
    )
    assert _break_glass(POLICY, a) is None


# --- the diff's last word and apply's guard ---------------------------------------------------

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
ALSO_PROTECTED = ("root-login", f"ops@{D}")


def _change(account: str) -> Change:
    return Change("leaver_access_left", "E1", account, "okta", "", "remove", "test")


def _row(account: str) -> dict:
    return {"account": account, "system": "okta", "app_id": "", "action": "remove"}


@SETTINGS
@given(
    st.one_of(spellings(LISTED + PATTERNED), spellings(ALSO_PROTECTED, bare=False)),
)
def test_both_guards_stop_every_spelling_of_every_protected_name(name):
    # names protected only through another name of their account are protected as written
    # (any case, spaces around), not as a bare username: `ops` is not `ops@meridian.example`
    diff_names = diff_mod._break_glass_strings(STATE, POLICY)
    apply_names = apply_mod._break_glass_names(STATE, POLICY)
    assert diff_mod._touches_break_glass(_change(name), POLICY, diff_names)
    assert apply_mod._touches_break_glass(_row(name), POLICY, apply_names)


@SETTINGS
@given(st.sampled_from((*NOT_PROTECTED, "ana", f"ANA@{D}", "root-login2", f"ops2@{D}")))
def test_neither_guard_stops_an_ordinary_account(name):
    diff_names = diff_mod._break_glass_strings(STATE, POLICY)
    apply_names = apply_mod._break_glass_names(STATE, POLICY)
    assert not diff_mod._touches_break_glass(_change(name), POLICY, diff_names)
    assert not apply_mod._touches_break_glass(_row(name), POLICY, apply_names)


def test_the_guards_collect_every_name_of_a_protected_account_and_only_those():
    want = {"root-login", f"vault.admin@{D}", f"ops@{D}", f"breakglass-9@{D}"}
    assert diff_mod._break_glass_strings(STATE, POLICY) == want
    assert apply_mod._break_glass_names(STATE, POLICY) == want


@SETTINGS
@given(spellings(LISTED + PATTERNED))
def test_apply_s_listed_check_takes_a_bare_name_or_a_full_address(name):
    assert apply_mod._is_listed(POLICY, name) is True
    assert apply_mod._is_listed(POLICY, f"x{name.strip()}") is False


# --- focused tests for what mutation testing found unchecked -------------------------------------


def test_a_bare_name_the_policy_lists_as_written_is_protected_as_written():
    bare = dataclasses.replace(POLICY, break_glass_accounts=frozenset({"rootbare"}))
    for a in (
        Account(system="slack", account="rootbare"),
        Account(system="github", account=" RootBare "),
        Account(system="google", account=f"x@{D}", email=f"x@{D}", aliases=("rootbare",)),
    ):
        assert _break_glass(bare, a) == "listed"
    assert _break_glass(bare, Account(system="slack", account=f"rootbare@{D}")) is None


# --- gap G2: every name of a protected account ----------------------------------------------------
#
# The owner's ruling (2026-11-19): break-glass protection covers every name of a protected
# account, aliases included. An account is protected when one of its names is, and then all its
# names are: its login, its email and each Google alias, wherever a record carries them, each also
# as a bare username (`ops` for `ops@meridian.example`) and as a company address
# (`root-login@meridian.example` for the login `root-login`), in any case matching treats as the
# same (it compares with str.casefold, so `OPS` and `op` plus a long s are `ops`), with spaces
# around. Today a bare username is protected only when the policy lists it or a pattern matches it,
# the policy compares with str.lower, and matching looks only at the names one record carries.
# Strict xfails; the source is unchanged.

G2_REASON = (
    "gap G2: break-glass protection does not yet cover every name of a protected account "
    "(the bare username or company address of a name protected through another name, a record "
    "in another system, case variants matching treats as the same)"
)
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


@pytest.mark.xfail(strict=True, reason=G2_REASON)
@pytest.mark.parametrize(
    "name", [f"Op{LONG_S}-Root@{D}", f"op{LONG_S}-root@{D}", f" OP{LONG_S}-ROOT@{D} "]
)
def test_g2_the_policy_takes_every_case_variant_matching_treats_as_the_same(name):
    assert name.strip().casefold() == f"ops-root@{D}"
    assert POLICY.break_glass_kind(name) == "listed"


@pytest.mark.xfail(strict=True, reason=G2_REASON)
@pytest.mark.parametrize("bare", ["rootbare", "RootBare"])
def test_g2_a_bare_name_the_policy_lists_protects_its_company_address(bare):
    policy = dataclasses.replace(POLICY, break_glass_accounts=frozenset({bare}))
    a = Account(system="slack", account=f"rootbare@{D}", email=f"rootbare@{D}")
    m = match_accounts([a], [], policy).matches.get(a.key)
    assert m is not None and m.rule == "break_glass"


@pytest.mark.xfail(strict=True, reason=G2_REASON)
@SETTINGS
@given(other_names(), st.sampled_from(("okta", "slack", "github")))
def test_g2_matching_marks_every_record_of_a_protected_account(name, system):
    other = _record(system, name)
    m = match_accounts([G2_GOOGLE, G2_OKTA, other], [], POLICY).matches.get(other.key)
    assert m is not None and (m.employee_id, m.rule) == ("", "break_glass")


@pytest.mark.xfail(strict=True, reason=G2_REASON)
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


@pytest.mark.xfail(strict=True, reason=G2_REASON)
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
