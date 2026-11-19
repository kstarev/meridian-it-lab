"""Property-based tests for the break-glass checks (hypothesis; AAC-007).

The rule (policy/break_glass.yaml): an account is protected if it is listed or fully matches a
pattern, case-insensitive, with spaces around it ignored, and also as a bare username
(`name` stands for `name@meridian.example`). Every name a protected account goes by (its login,
its email, a Google alias) is protected too. Four places check it: the policy itself, matching
(`match._break_glass`), the diff's last word (`diff._touches_break_glass`) and apply's final
guard (`apply._touches_break_glass`). Each is checked here against the same rule, on made-up
names. The end-to-end check (no write reaches a protected account) is in
test_apply_properties.py.

The runs are fixed (derandomize) and keep no example database.
"""

import dataclasses
import re

from hypothesis import given, settings
from hypothesis import strategies as st

from meridian_access import apply as apply_mod
from meridian_access import diff as diff_mod
from meridian_access.collect import ActualState
from meridian_access.diff import Change
from meridian_access.match import _break_glass
from meridian_access.models import Account
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
