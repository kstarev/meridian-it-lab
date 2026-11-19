"""Property-based tests for identity matching (hypothesis).

Matching decides who owns each account, and the planner removes access on that word, so a
false match is worse than none (match.py). The rules checked here:

- every account ends in exactly one of matched, unmatched and ambiguous, and the result does
  not depend on the order the accounts come in;
- a break-glass account is never matched to a person, whatever else it carries;
- the old record of a rehired person never owns an account;
- a rule counts only when it gives exactly one person: two people who share the trait leave
  the account ambiguous, never matched to either;
- an address at the company domain that rules 1-3 don't place is never placed by a name;
- a number in a username must agree with the person's email number;
- rule 2 ignores case, accents and a `+tag`, and nothing else.

The last one does not hold: `fold` drops every character NFKD can't turn into ASCII, so an
address with an extra zero-width or Cyrillic character is matched to the person without it.
That test is a strict xfail, listed in the overnight gaps file; the source is unchanged.

Made-up people at meridian.example only, no world, no network. The runs are fixed
(derandomize) and keep no example database.
"""

import random
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access.match import (
    CONFIDENCE,
    fold,
    match_accounts,
    norm_email,
    username_templates,
)
from meridian_access.models import Account
from meridian_access.policy.load import load_policy
from meridian_dataset.access.model import DOMAIN, HrisRecord

SETTINGS = settings(derandomize=True, max_examples=150, database=None, deadline=None)
POLICY = load_policy()

# A small pool so that people often share a first name, a family name or a whole name.
GIVEN = ("Ana", "Ben", "Chloé", "Dávid", "Ørjan", "Zoë", "Ana")
FAMILY = ("Smith", "Müller", "O'Neil", "Łukasz", "Ng", "Smith")
APPS = ("github", "jira", "figma")


def record(eid, given, family, email, *, preferred="", status="active", end=""):
    return HrisRecord(
        employee_id=eid,
        given_name=given,
        family_name=family,
        preferred_name=preferred,
        work_email=email,
        personal_email=f"{eid.lower()}@mail.example",
        job_title="Engineer",
        department="Engineering",
        team="Core",
        manager_id="",
        country="DE",
        hub="x",
        employment_type="employee",
        status=status,
        start_date="2025-01-01",
        end_date=end,
        leave_start="",
        leave_end="",
    )


def email_of(given, family, number):
    return f"{fold(given)}.{fold(family)}{number or ''}@{DOMAIN}".replace("'", "")


@st.composite
def people(draw, min_size=1, max_size=6):
    """HRIS records with unique ids; emails may collide (two people, one address)."""
    rows = draw(
        st.lists(
            st.tuples(st.sampled_from(GIVEN), st.sampled_from(FAMILY), st.sampled_from((0, 0, 2))),
            min_size=min_size,
            max_size=max_size,
        )
    )
    return [record(f"E{i:03}", g, f, email_of(g, f, n)) for i, (g, f, n) in enumerate(rows)]


def account(system, name, **kw):
    app_id = kw.pop("app_id", "" if system in ("okta", "google", "slack") else system)
    kw.setdefault("email", name if "@" in name else "")
    return Account(system=system, account=name, app_id=app_id, **kw)


def shapes_for(r: HrisRecord):
    """Accounts a person could plausibly have, in the shapes the collectors make."""
    local = r.work_email.split("@")[0]
    t = username_templates(r.given_name, r.family_name)
    return [
        account("okta", r.work_email),
        account("google", r.work_email.upper()),
        account("google", f"{local}+tag@{DOMAIN}"),
        account("slack", local),
        account("slack", local, email=f"{local}@mail.example", display="Some One"),
        account(APPS[0], t["flast"] if t else "x", email=""),
        account(APPS[1], "row-7", email="", display=f"{r.given_name} {r.family_name}"),
        account(APPS[2], "row-8", email="", given=r.given_name, family=r.family_name),
    ]


OUTSIDERS = [
    account("okta", f"stranger@{DOMAIN}", display="Ana Smith"),
    account("slack", "nobody"),
    account("figma", "Ana Smith", email=""),
    account("google", "breakglass-01@meridian.example"),
    account("okta", "BREAKGLASS-77@MERIDIAN.EXAMPLE"),
    account("slack", "breakglass-02"),
    account("google", "ops@meridian.example", aliases=("breakglass-03@meridian.example",)),
]


@st.composite
def worlds(draw):
    records = draw(people())
    pool = [a for r in records for a in shapes_for(r)] + OUTSIDERS
    accounts = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=14))
    # one account per key: two accounts with one key (a repeated export row) are taken in
    # input order, which is deterministic but not order-free
    return records, list({a.key: a for a in accounts}.values())


def keys_of(result):
    return list(result.matches), list(result.unmatched), list(result.ambiguous)


# --- the result ---------------------------------------------------------------------------------


@SETTINGS
@given(worlds(), st.randoms(use_true_random=False))
def test_every_account_lands_in_exactly_one_list_whatever_the_order(world, rnd):
    records, accounts = world
    result = match_accounts(accounts, records, POLICY)
    matched, unmatched, ambiguous = keys_of(result)
    every = matched + unmatched + ambiguous
    assert len(every) == len(set(every)), "an account is in two lists"
    assert set(every) == {a.key for a in accounts}
    shuffled = list(accounts)
    rnd.shuffle(shuffled)
    again = match_accounts(shuffled, list(reversed(records)), POLICY)
    assert again.matches == result.matches
    assert again.unmatched == result.unmatched and again.ambiguous == result.ambiguous


@SETTINGS
@given(worlds())
def test_a_match_names_a_real_person_with_the_rule_s_confidence(world):
    records, accounts = world
    ids = {r.employee_id for r in records}
    for m in match_accounts(accounts, records, POLICY).matches.values():
        assert m.confidence == CONFIDENCE[m.rule]
        if m.rule == "break_glass":
            assert m.employee_id == "" and m.note in ("listed", "pattern")
        else:
            assert m.employee_id in ids and m.note == ""


# --- break-glass ----------------------------------------------------------------------------------

BREAK_GLASS = (
    "breakglass-01@meridian.example",
    "breakglass-02@meridian.example",
    "breakglass-9@meridian.example",
)


def _spellings(value: str):
    local = value.split("@")[0]
    return st.sampled_from(
        (value, value.upper(), f"  {value}\t", local, local.upper(), f" {local} ")
    )


@SETTINGS
@given(
    st.sampled_from(BREAK_GLASS).flatmap(_spellings),
    st.sampled_from(("account", "email", "alias")),
    st.sampled_from(("okta", "google", "slack", "github")),
    people(),
)
def test_a_break_glass_account_is_never_matched_to_a_person(name, where, system, records):
    # the account also carries a real person's address, so a rule would match it if it ran
    victim = records[0].work_email
    if where == "account":
        a = account(system, name, email=victim if "@" not in name else name, aliases=(victim,))
    elif where == "email":
        a = account(system, victim, email=name)
    else:
        a = account(system, victim, email=victim, aliases=(name,))
    m = match_accounts([a], records, POLICY).matches[a.key]
    assert (m.employee_id, m.rule) == ("", "break_glass")
    assert m.note == (
        "listed" if name.strip().split("@")[0].lower() != "breakglass-9" else "pattern"
    )


@SETTINGS
@given(people(), st.sampled_from(("breakglass-x@meridian.example", "breakglass-01@mail.example")))
def test_a_near_miss_of_the_break_glass_list_is_matched_as_usual(records, near):
    a = account("okta", records[0].work_email, aliases=(near,))
    m = match_accounts([a], records, POLICY).matches.get(a.key)
    assert m is None or m.rule != "break_glass"


# --- rehires ----------------------------------------------------------------------------------


@SETTINGS
@given(people(), st.data())
def test_the_old_record_of_a_rehire_never_owns_an_account(records, data):
    current = data.draw(st.sampled_from(records))
    old = record(
        "E999",
        current.given_name,
        current.family_name,
        current.work_email,
        status="terminated",
        end="2024-06-30",
    )
    pool = shapes_for(current)
    accounts = data.draw(st.lists(st.sampled_from(pool), min_size=1, max_size=6))
    result = match_accounts(accounts, [*records, old], POLICY)
    assert "E999" not in {m.employee_id for m in result.matches.values()}
    # and with the old record alone (no current one), it is a candidate like anyone else
    alone = match_accounts([account("okta", old.work_email)], [old], POLICY)
    assert alone.owner(("okta", "", old.work_email)) == "E999"


# --- one person or none -------------------------------------------------------------------------


@SETTINGS
@given(st.sampled_from(GIVEN), st.sampled_from(FAMILY), st.sampled_from(("display", "names")))
def test_two_people_with_one_name_leave_a_name_only_account_ambiguous(given_, family, how):
    a1 = record("E001", given_, family, email_of(given_, family, 0))
    a2 = record("E002", given_, family, email_of(given_, family, 2))
    kw = (
        {"display": f"{given_} {family}"}
        if how == "display"
        else {"given": given_, "family": family}
    )
    a = account("figma", "row-1", email="", **kw)
    both = match_accounts([a], [a1, a2], POLICY)
    assert a.key in both.ambiguous and a.key not in both.matches
    one = match_accounts([a], [a1], POLICY)
    assert one.matches[a.key].employee_id == "E001"
    assert one.matches[a.key].rule == "name"


@SETTINGS
@given(st.sampled_from(GIVEN), st.sampled_from(FAMILY), st.booleans())
def test_two_people_with_one_address_are_never_matched_by_it(given_, family, tagged):
    email = email_of(given_, family, 0)
    one, two = record("E001", given_, family, email), record("E002", given_, family, email)
    local = email.split("@")[0]
    address = f"{local}+x@{DOMAIN}" if tagged else email.upper()
    for a in (account("okta", address), account("slack", local)):
        result = match_accounts([a], [one, two], POLICY)
        assert a.key not in result.matches
        assert a.key in result.ambiguous
        alone = match_accounts([a], [one], POLICY).matches[a.key]
        expected = (
            "slack_username"
            if a.system == "slack"
            else ("normalised_email" if tagged else "exact_email")
        )
        assert (alone.employee_id, alone.rule) == ("E001", expected)


def test_an_export_username_shared_by_two_people_needs_the_row_names():
    # "anas" is the firstl username of both Ana Smith and Ana Smyth
    r1 = record("E101", "Ana", "Smith", email_of("Ana", "Smith", 0))
    r2 = record("E102", "Ana", "Smyth", email_of("Ana", "Smyth", 0))
    username = "anas"  # firstl for both
    bare = account("github", username, email="")
    result = match_accounts([bare], [r1, r2], POLICY)
    assert bare.key in result.ambiguous
    named = account("github", username, email="", family="Smyth")
    m = match_accounts([named], [r1, r2], POLICY).matches[named.key]
    assert (m.employee_id, m.rule) == ("E102", "username_names")
    wrong = account("github", username, email="", family="Jones")
    assert wrong.key in match_accounts([wrong], [r1, r2], POLICY).ambiguous


# --- the company domain stops the name rules ----------------------------------------------------


@SETTINGS
@given(people(), st.sampled_from(("display", "names", "slack")))
def test_an_unknown_company_address_is_never_placed_by_a_name(records, how):
    r = records[0]
    address = f"zz-unknown@{DOMAIN}"
    if how == "display":
        a = account("okta", address, display=f"{r.given_name} {r.family_name}")
    elif how == "names":
        a = account("google", address, given=r.given_name, family=r.family_name)
    else:
        a = account("slack", r.work_email.split("@")[0], email=address)
    result = match_accounts([a], records, POLICY)
    assert a.key not in result.matches


@SETTINGS
@given(people())
def test_a_slack_account_with_an_outside_address_needs_agreeing_names(records):
    r = records[0]
    local = r.work_email.split("@")[0]
    if sum(x.work_email.split("@")[0] == local for x in records) > 1:
        return
    disagree = account("slack", local, email="x@mail.example", given="Quentin", family="Zed")
    assert disagree.key not in match_accounts([disagree], records, POLICY).matches
    agree = account(
        "slack", local, email="x@mail.example", given=r.given_name, family=r.family_name
    )
    m = match_accounts([agree], records, POLICY).matches[agree.key]
    assert (m.employee_id, m.rule) == (r.employee_id, "slack_username")


# --- the number in a username ------------------------------------------------------------------


@SETTINGS
@given(
    st.sampled_from(GIVEN),
    st.sampled_from(FAMILY),
    st.sampled_from((0, 2, 3)),
    st.sampled_from((0, 2, 3)),
)
def test_a_number_in_a_username_must_agree_with_the_email_number(given_, family, has, typed):
    r = record("E001", given_, family, email_of(given_, family, has))
    t = username_templates(given_, family)
    a = account("jira", f"{t['flast']}{typed or ''}", email="")
    m = match_accounts([a], [r], POLICY).matches.get(a.key)
    if has == typed:
        assert m is not None and (m.employee_id, m.rule) == ("E001", "username")
    else:
        assert m is None


# --- rule 2: case, accents and a +tag, nothing else ---------------------------------------------

ACCENTS = ("\u0301", "\u0308", "\u030a", "\u0327")  # acute, diaeresis, ring, cedilla


def _restyle(email: str, rnd: random.Random) -> str:
    """The same address with random case, an accent on one letter and maybe a +tag."""
    local, _, domain = email.partition("@")
    chars = [c.upper() if rnd.random() < 0.5 else c for c in local]
    i = rnd.randrange(len(chars))
    if chars[i].isalpha():
        chars[i] = unicodedata.normalize("NFC", chars[i] + rnd.choice(ACCENTS))
    tag = "+" + rnd.choice(("x", "old", "2")) if rnd.random() < 0.5 else ""
    return "".join(chars) + tag + "@" + domain.upper()


@SETTINGS
@given(people(), st.randoms(use_true_random=False))
def test_case_accents_and_a_tag_find_the_same_one_person(records, rnd):
    r = records[0]
    if sum(x.work_email == r.work_email for x in records) > 1:
        return
    styled = _restyle(r.work_email, rnd)
    assert norm_email(styled) == norm_email(r.work_email)
    a = account("okta", styled)
    m = match_accounts([a], records, POLICY).matches[a.key]
    assert m.employee_id == r.employee_id
    assert m.rule in ("exact_email", "normalised_email")


# Characters that are not a case change, an accent or a tag. Rule 2 says it ignores only
# those three, so each of these makes a different address.
OTHER_CHARACTERS = {
    "zero-width space": "\u200b",
    "soft hyphen": "\u00ad",
    "cyrillic zhe": "\u0436",
    "cjk": "\u4e2d",
}


@pytest.mark.xfail(
    strict=True,
    reason="gap: fold() drops or rewrites characters that are not accents, so rule 2 "
    "matches a different address to the person",
)
@pytest.mark.parametrize("extra", OTHER_CHARACTERS.values(), ids=OTHER_CHARACTERS)
def test_rule_2_does_not_ignore_characters_other_than_accents(extra):
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    a = account("google", f"ana.smith{extra}@{DOMAIN}")
    assert a.key not in match_accounts([a], [r], POLICY).matches


# --- focused tests for what mutation testing found unchecked -------------------------------------


def test_a_username_with_a_1_does_not_match_a_person_whose_email_has_no_number():
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    for name, owner in (("asmith", "E001"), ("asmith1", ""), ("asmith2", "")):
        a = account("jira", name, email="")
        assert match_accounts([a], [r], POLICY).owner(a.key) == owner, name


@pytest.mark.parametrize(
    ("given_", "family", "display", "agrees"),
    [
        ("", "Smith", "", True),
        ("", "Jones", "", False),
        ("Ana", "", "", True),
        ("A", "", "", True),
        ("B", "", "", False),
        ("An", "", "", False),  # a longer prefix is not an initial
        ("Ana", "Jones", "", False),
        ("B", "Smith", "", False),
        ("", "", "Ana Smith", True),
        ("", "", "Ana Jones", False),
        ("Ana", "Smith", "Ana Jones", False),
    ],
)
def test_the_row_names_must_agree_with_the_one_person_a_username_points_to(
    given_, family, display, agrees
):
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    a = account("jira", "asmith", email="", given=given_, family=family, display=display)
    m = match_accounts([a], [r], POLICY).matches.get(a.key)
    if agrees:
        assert m is not None and (m.employee_id, m.rule) == ("E001", "username")
    else:
        assert m is None or m.rule == "name"  # only the name rule may still place it


def test_an_account_listed_twice_is_listed_once():
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    lost = account("figma", "zz-row", email="")
    shared = [
        record("E002", "Ben", "Ng", f"ben.ng@{DOMAIN}"),
        record("E003", "Ben", "Ng", f"ben.ng2@{DOMAIN}"),
    ]
    many = account("figma", "row-9", email="", display="Ben Ng")
    result = match_accounts([lost, lost, many, many], [r, *shared], POLICY)
    assert result.unmatched == [lost.key] and result.ambiguous == [many.key]


def test_a_google_alias_with_a_tag_or_in_capitals_still_places_the_account():
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    for alias in (f"ana.smith+old@{DOMAIN}", f"ANA.SMITH@{DOMAIN}", f"ana.smith@{DOMAIN}"):
        g = account("google", f"zz@{DOMAIN}", aliases=(alias,))
        m = match_accounts([g], [r], POLICY).matches[g.key]
        assert (m.employee_id, m.rule) == ("E001", "google_alias"), alias


def test_an_address_that_is_another_google_user_s_alias_finds_that_user_s_person():
    r = record("E001", "Ana", "Smith", f"ana.smith@{DOMAIN}")
    for primary in (f"ana.smith@{DOMAIN}", f"ana.smith+g@{DOMAIN}", f"Ana.Smith@{DOMAIN}"):
        g = account("google", primary, aliases=(f"as@{DOMAIN}",))
        o = account("okta", f"as@{DOMAIN}")
        m = match_accounts([g, o], [r], POLICY).matches[o.key]
        assert (m.employee_id, m.rule) == ("E001", "google_alias"), primary
