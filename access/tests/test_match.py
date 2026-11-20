"""Identity matching, rules only."""

import pytest

from meridian_access.collect import actual_state
from meridian_access.match import fold, match_accounts, norm_email, username_schemes
from meridian_access.models import Account
from meridian_dataset.access.model import HrisRecord


def rec(eid, given, family, email=None, preferred="", start="2025-01-01", status="active", end=""):
    return HrisRecord(
        employee_id=eid,
        given_name=given,
        family_name=family,
        preferred_name=preferred,
        work_email=email or f"{fold(given)}.{fold(family)}@meridian.example",
        personal_email=f"{fold(given)}@mail.example",
        job_title="Engineer",
        department="Engineering",
        team="Core",
        manager_id="",
        country="DE",
        hub="x",
        employment_type="employee",
        status=status,
        start_date=start,
        end_date=end,
        leave_start="",
        leave_end="",
    )


def acct(system, account, **kw):
    app_id = kw.pop("app_id", system if system not in ("okta", "google", "slack") else "")
    kw.setdefault("email", account if "@" in account else "")
    return Account(system=system, account=account, app_id=app_id, **kw)


@pytest.fixture(scope="module")
def result(world_dir, policy):
    from meridian_access.desired import load_hris

    records, _ = load_hris(world_dir / "hris.json")
    state = actual_state(world_dir, policy)
    return state, records, match_accounts(state.accounts, records, policy)


def test_rules_only_precision_and_recall_on_the_tuning_world(world, result):
    from meridian_access.score import score_matching

    score = score_matching(result[2], world.answer_key.owners)
    print(f"rules only: precision {score.precision.cell()}, recall {score.recall.cell()}")
    assert score.precision.value >= 0.98
    assert score.recall.value >= 0.95  # reported; the rest are renamed people, for the AI
    assert score.unowned_left_unmatched.successes == score.unowned_left_unmatched.total
    assert score.not_in_key == 0  # every matched account is one the key knows about


def test_matching_is_deterministic_and_never_reads_the_answer_key(world_dir, policy, result):
    state, records, first = result
    again = match_accounts(list(reversed(state.accounts)), records, policy)
    assert again.matches == first.matches
    assert sorted(again.unmatched) == sorted(first.unmatched)
    assert not (world_dir / "answer_key.json").exists()


def test_superseded_records_never_own_an_account(world, policy, result):
    from meridian_access.desired import desired_state

    _, records, res = result
    superseded = {
        p.employee_id
        for p in desired_state(records, world.hris.events, policy).people
        if p.category == "superseded"
    }
    assert superseded  # the tuning world has rehires
    assert not superseded & {m.employee_id for m in res.matches.values()}


def test_every_account_has_exactly_one_outcome(result):
    state, _, res = result
    keys = {a.key for a in state.accounts}
    outcomes = [*res.matches, *res.unmatched, *res.ambiguous]
    assert len(outcomes) == len(set(outcomes)) == len(keys)


# ------------------------------------------------------------------------------ rules


def test_rule_1_exact_email_is_case_insensitive(policy):
    people = [rec("E1", "Ann", "Lee"), rec("E2", "Bo", "Li")]
    res = match_accounts([acct("google", "ANN.LEE@Meridian.Example")], people, policy)
    m = res.matches[("google", "", "ANN.LEE@Meridian.Example")]
    assert (m.employee_id, m.rule) == ("E1", "exact_email")


def test_rule_2_plus_tags_and_accents_but_not_dots(policy):
    people = [rec("E1", "José", "García", email="jose.garcia@meridian.example")]
    for login in (
        "jose.garcia+sales@meridian.example",
        "josé.garcía@meridian.example",
        "JOSE.GARCIA+x@Meridian.Example",
    ):
        res = match_accounts([acct("okta", login)], people, policy)
        m = res.matches[("okta", "", login)]
        assert (m.employee_id, m.rule) == ("E1", "normalised_email")
    # a different dot placement is a different address: no match
    dotless = acct("okta", "josegarcia@meridian.example")
    res = match_accounts([dotless], people, policy)
    assert res.unmatched == [dotless.key] and not res.matches


def test_rule_2_refuses_an_address_with_a_zero_width_space(policy):
    # AAC-016 (gap G1): a zero-width space before the `@` is not case, an accent or a tag
    people = [rec("E1", "Ana", "Smith", email="ana.smith@meridian.example")]
    lookalike = acct("google", "ana.smith\u200b@meridian.example")
    res = match_accounts([lookalike], people, policy)
    assert res.unmatched == [lookalike.key] and not res.matches and not res.ambiguous


@pytest.mark.parametrize(
    "account",
    [
        # a display name that is the person's: rule 6 is not tried
        acct("google", "ana.smith\u200b@meridian.example", display="Ana Smith"),
        # a look-alike of the company domain is not an outside address the names can place
        acct("okta", "ana.smith@meridian\u200b.example", display="Ana Smith"),
        acct(
            "slack",
            "ana.smith",
            email="ana.smith@meridian\u200b.example",
            given="Ana",
            family="Smith",
            display="Ana Smith",
        ),
        # full-width and mathematical look-alikes: NFKD would rewrite them to ASCII
        acct("google", "\uff41na.smith@meridian.example"),
        acct("google", "\U0001d41ana.smith@meridian.example"),
        # rule 3's normalised branch refuses the same addresses
        acct("google", "zz@meridian.example", aliases=("ana.smith\u200b@meridian.example",)),
    ],
    ids=["display", "domain", "slack", "full-width", "mathematical", "alias"],
)
def test_no_later_rule_places_an_address_rule_2_refuses(policy, account):
    people = [rec("E1", "Ana", "Smith", email="ana.smith@meridian.example")]
    res = match_accounts([account], people, policy)
    assert res.unmatched == [account.key] and not res.matches


def test_an_exact_google_alias_still_places_an_address_rule_2_refuses(policy):
    # rule 3 with an exact alias is Google's own record, not folding
    people = [rec("E1", "Ana", "Smith", email="ana.smith@meridian.example")]
    g = acct("google", "ana.smith\u200b@meridian.example", aliases=("ana.smith@meridian.example",))
    m = match_accounts([g], people, policy).matches[g.key]
    assert (m.employee_id, m.rule) == ("E1", "google_alias")


def test_a_numbered_email_is_not_matched(policy):
    # HRIS numbers its duplicate addresses (ann.lee4); "ann.lee2" is not guessed from them
    people = [
        rec("E1", "Ann", "Lee", start="2020-01-01"),
        rec("E2", "Ann", "Lee", email="ann.lee4@meridian.example", start="2023-01-01"),
        rec("E3", "Ann", "Lee", email="ann.lee5@meridian.example", start="2024-01-01"),
    ]
    login = "ann.lee2@meridian.example"
    res = match_accounts([acct("okta", login)], people, policy)
    assert res.unmatched == [("okta", "", login)] and not res.matches


def test_a_company_email_that_rules_1_to_3_miss_stops_there(policy):
    # the display name is exact, but the address is a company address nobody has
    people = [rec("E1", "Ann", "Lee")]
    row = acct("app-one", "ghost@meridian.example", display="Ann Lee", given="Ann", family="Lee")
    res = match_accounts([row], people, policy)
    assert res.unmatched == [row.key] and not res.matches
    # the same names with an outside-domain email still reach the name rule
    outside = acct("app-two", "ann@gmail.example", display="Ann Lee", app_id="app-two")
    res = match_accounts([outside], people, policy)
    assert res.matches[outside.key].rule == "name"


def test_a_slack_account_with_an_outside_email_needs_agreeing_names(policy):
    people = [rec("E1", "Ann", "Lee")]
    agree = acct("slack", "ann.lee", email="ann@gmail.example", display="Ann Lee")
    disagree = acct("slack", "ann.lee", email="x@gmail.example", display="Someone Else")
    nameless = acct("slack", "ann.lee", email="y@gmail.example")
    res = match_accounts([agree], people, policy)
    assert res.matches[agree.key].rule == "slack_username"
    for bad in (disagree, nameless):
        res = match_accounts([bad], people, policy)
        assert res.unmatched == [bad.key] and not res.matches


def test_rule_3_google_aliases(policy):
    people = [rec("E1", "Ann", "Lee"), rec("E2", "Bo", "Li")]
    google = acct("google", "ann.new@meridian.example", aliases=("bo.li@meridian.example",))
    res = match_accounts([google], people, policy)
    m = res.matches[google.key]
    assert (m.employee_id, m.rule) == ("E2", "google_alias")
    # another system's address that is only a Google alias belongs to that Google user
    other = acct("okta", "alias.only@meridian.example")
    g = acct("google", "ann.lee@meridian.example", aliases=("alias.only@meridian.example",))
    res = match_accounts([g, other], people, policy)
    assert res.matches[other.key].employee_id == "E1"


def test_rule_4_slack_username_is_the_local_part(policy):
    people = [rec("E1", "Ann", "Lee")]
    res = match_accounts([acct("slack", "ann.lee")], people, policy)
    assert res.matches[("slack", "", "ann.lee")].rule == "slack_username"


def test_rule_5_usernames_under_known_schemes(policy):
    people = [rec("E1", "Ann", "Lee"), rec("E2", "Bo", "Okafor")]
    cases = {
        "alee": "E1",
        "ann.l": "E1",
        "ann_lee": "E1",
        "bokafor": "E2",
        "okaforb": "E2",
        "bo.okafor": "E2",
    }
    for username, owner in cases.items():
        res = match_accounts([acct("app-one", username)], people, policy)
        assert res.matches[("app-one", "app-one", username)].employee_id == owner, username
    assert "alee" in username_schemes("Ann", "Lee") and "ann.l" in username_schemes("Ann", "Lee")


def test_namesake_tie_break_uses_the_rows_name_columns(policy):
    people = [rec("E1", "Chloe", "Okafor"), rec("E2", "Camila", "Okafor")]  # both are cokafor
    chloe = acct("app-one", "cokafor", given="Chloe", family="Okafor")
    camila = acct("app-one", "cokafor", app_id="app-two", given="camila", family="OKAFOR")
    by_display = acct("app-one", "cokafor", app_id="app-three", display="Camila Okafor")
    res = match_accounts([chloe, camila, by_display], people, policy)
    assert res.matches[chloe.key].employee_id == "E1"
    assert res.matches[camila.key].employee_id == "E2"
    assert res.matches[by_display.key].employee_id == "E2"
    # no names to choose with: ambiguous, never a guess
    bare = acct("app-one", "cokafor", app_id="app-four")
    res = match_accounts([bare], people, policy)
    assert res.ambiguous == [bare.key] and not res.matches


def test_numbered_namesakes_are_not_split_by_their_email_number(policy):
    # two people fit "cokafor"; HRIS numbers one of them, but the number is not a tie-break
    people = [
        rec("E1", "Chloe", "Okafor"),
        rec("E2", "Chloe", "Okafor", email="chloe.okafor2@meridian.example"),
    ]
    for name in ("cokafor", "cokafor2"):
        row = acct("app-one", name)
        res = match_accounts([row], people, policy)
        assert res.ambiguous == [row.key] and not res.matches


def test_a_number_in_a_username_must_agree_with_the_email_number(policy):
    people = [rec("E1", "Ann", "Lee", email="ann.lee2@meridian.example"), rec("E2", "Bo", "Li")]
    ok = acct("app-one", "alee2")
    wrong = acct("app-one", "alee")  # no number, but the person's email has one
    extra = acct("app-one", "bli3")  # a number, but the person's email has none
    res = match_accounts([ok, wrong, extra], people, policy)
    assert res.matches[ok.key].employee_id == "E1"
    assert sorted(res.unmatched) == sorted([wrong.key, extra.key])


def test_the_rows_first_name_is_exact_or_a_single_initial(policy):
    people = [rec("E1", "Alice", "Lee")]
    initial = acct("app-one", "alee", given="A", family="Lee")
    prefix = acct("app-one", "alee", app_id="app-two", given="Ali", family="Lee")
    full = acct("app-one", "alee", app_id="app-three", given="Alice", family="Lee")
    res = match_accounts([initial, prefix, full], people, policy)
    assert res.matches[initial.key].employee_id == "E1"
    assert res.matches[full.key].employee_id == "E1"
    assert prefix.key in res.unmatched


def test_accented_and_preferred_names_pick_the_namesake(policy):
    people = [rec("E1", "Zoë", "Müller"), rec("E2", "Zoe", "Mueller", preferred="Zo")]
    row = acct("app-one", "zmuller", given="Zoë", family="Müller")
    assert match_accounts([row], people, policy).matches[row.key].employee_id == "E1"


def test_a_username_whose_names_disagree_is_not_matched(policy):
    people = [rec("E1", "Ann", "Lee")]
    row = acct("app-one", "alee", given="Alex", family="Lee")  # a different A. Lee, not in HRIS
    res = match_accounts([row], people, policy)
    assert res.unmatched == [row.key]


def test_rule_6_exact_display_name_only_when_unique(policy):
    people = [
        rec("E1", "Ann", "Lee"),
        rec("E2", "Bo", "Li"),
        rec("E3", "Bo", "Li", email="bo.li2@meridian.example"),
    ]
    ok = acct("app-one", "lee-svc", display="Lee, Ann", email="")
    twins = acct("app-two", "li-svc", app_id="app-two", display="Bo Li")
    res = match_accounts([ok, twins], people, policy)
    assert res.matches[ok.key].employee_id == "E1" and res.matches[ok.key].rule == "name"
    assert res.ambiguous == [twins.key]


def test_unknown_accounts_are_unmatched(policy):
    res = match_accounts(
        [acct("okta", "ghost@meridian.example")], [rec("E1", "Ann", "Lee")], policy
    )
    assert res.unmatched == [("okta", "", "ghost@meridian.example")] and not res.matches


# ------------------------------------------------------------------------ break-glass


def test_break_glass_is_marked_and_never_matched_to_a_person(policy):
    people = [rec("E1", "Break", "Glass", email="breakglass-01@meridian.example")]
    listed = acct("okta", "breakglass-01@meridian.example")
    pattern = acct("google", "breakglass-77@meridian.example")
    res = match_accounts([listed, pattern], people, policy)
    for acc, kind in ((listed, "listed"), (pattern, "pattern")):
        m = res.matches[acc.key]
        assert (m.employee_id, m.rule, m.note) == ("", "break_glass", kind)
    assert not res.unmatched and not res.ambiguous


def test_a_bare_break_glass_username_is_marked_too(policy):
    people = [rec("E1", "Break", "Glass", email="breakglass-01@meridian.example")]
    listed = acct("app-one", "breakglass-01")
    pattern = acct("app-one", "breakglass-42", app_id="app-two")
    res = match_accounts([listed, pattern], people, policy)
    assert res.matches[listed.key].note == "listed" and res.matches[listed.key].employee_id == ""
    assert res.matches[pattern.key].note == "pattern"


def test_a_google_alias_that_is_break_glass_marks_the_account(policy):
    people = [rec("E1", "Ann", "Lee", email="ann.lee@meridian.example")]
    google = acct("google", "ann.lee@meridian.example", aliases=("breakglass-01@meridian.example",))
    res = match_accounts([google], people, policy)
    assert res.matches[google.key].rule == "break_glass"


def test_the_tuning_break_glass_accounts_are_all_marked(world, result):
    _, _, res = result
    marked = {k[2] for k, m in res.matches.items() if m.rule == "break_glass"}
    assert set(world.answer_key.break_glass_accounts) <= marked
    assert all(m.employee_id == "" for m in res.matches.values() if m.rule == "break_glass")


def test_helpers():
    assert fold("Çelik") == "celik"
    assert norm_email("Jo.Sé+tag@Meridian.Example") == "jo.se@meridian.example"
    assert norm_email("Øystein.Æsir+x@meridian.example") == "oystein.aesir@meridian.example"
    for extra in ("\u200b", "\u00ad", "\u0436", "\u4e2d", "\uff41", "\U0001d41a"):
        assert norm_email(f"ana{extra}@meridian.example") is None, ascii(extra)


def test_fold_transliterates_letters_nfkd_cannot_decompose():
    assert fold("Østergaard") == "ostergaard"
    assert fold("Æsir ß Łukasz Đorđe Þór Œuvre") == "aesir ss lukasz dorde thor oeuvre"
    assert fold("ø æ ł đ þ œ") == "o ae l d th oe"


# --- gap G6: case folding and NFD turn some look-alikes into plain ASCII ------------------------
#
# Found in review (2026-11-20, finding F1). Rule 1 (the exact address) and rule 3's exact alias
# branch key an address with str.casefold, which turns the ligatures U+FB00-FB06, the long s
# U+017F and the Kelvin sign U+212A into plain ASCII letters. So a look-alike made with one of
# them is placed on the real person as `exact_email` (or `google_alias`). Rule 2 refuses the
# ligatures and the long s, but rule 1 runs first; and rule 2 itself lets the Kelvin sign through,
# since str.lower and NFD both make it `k` with no accent to strip. AAC-016 says ligatures and
# other look-alikes are refused and stay orphans. These are strict xfails until the owner rules.

G6_GAP = "gap G6: case folding or NFD turns a look-alike into ASCII, so rules 1-3 place it"
g6 = pytest.mark.xfail(strict=True, reason=G6_GAP)

# (given, family, the person's address, a look-alike of it)
G6_LOOKALIKES = {
    "ff ligature": ("Effie", "Laffer", "effie.laffer", "e\ufb00ie.laffer"),
    "fi ligature": ("Fiona", "Kerr", "fiona.kerr", "\ufb01ona.kerr"),
    "fl ligature": ("Flora", "Diaz", "flora.diaz", "\ufb02ora.diaz"),
    "ffi ligature": ("Ffion", "Jones", "ffion.jones", "\ufb03on.jones"),
    "ffl ligature": ("Ana", "Ruffle", "ana.ruffle", "ana.ru\ufb04e"),
    "long s t ligature": ("Ana", "Stone", "ana.stone", "ana.\ufb05one"),
    "st ligature": ("Ana", "Stone", "ana.stone", "ana.\ufb06one"),
    "long s": ("Ana", "Smith", "ana.smith", "ana.\u017fmith"),
    "kelvin sign": ("Fiona", "Kerr", "fiona.kerr", "fiona.\u212aerr"),
}
G6_CASES = pytest.mark.parametrize(
    ("given", "family", "local", "lookalike"), G6_LOOKALIKES.values(), ids=G6_LOOKALIKES
)
G6_D = "@meridian.example"


def test_g6_each_look_alike_case_folds_to_the_persons_address():
    for _, _, local, lookalike in G6_LOOKALIKES.values():
        assert not lookalike.isascii() and lookalike.casefold() == local


@g6
@G6_CASES
def test_g6_rule_1_does_not_place_a_case_folding_look_alike(
    policy, given, family, local, lookalike
):
    people = [rec("E1", given, family, email=local + G6_D)]
    a = acct("okta", lookalike + G6_D)
    res = match_accounts([a], people, policy)
    assert res.unmatched == [a.key] and not res.matches and not res.ambiguous


@g6
@G6_CASES
def test_g6_rule_3_does_not_place_a_case_folding_look_alike_alias(
    policy, given, family, local, lookalike
):
    people = [rec("E1", given, family, email=local + G6_D)]
    g = acct("google", "zz" + G6_D, aliases=(lookalike + G6_D,))
    res = match_accounts([g], people, policy)
    assert res.unmatched == [g.key] and not res.matches and not res.ambiguous


@g6
@pytest.mark.parametrize(
    ("alias", "lookalike"),
    [("fiona.k", "\ufb01ona.k"), ("ana.s", "ana.\u017f"), ("fiona.k", "fiona.\u212a")],
    ids=["fi ligature", "long s", "kelvin sign"],
)
def test_g6_rule_3_does_not_place_a_look_alike_of_a_google_alias(policy, alias, lookalike):
    # the Google user's alias is the person's; an Okta login that only case-folds to it is not
    given, family = ("Fiona", "Kerr") if alias == "fiona.k" else ("Ana", "Smith")
    people = [rec("E1", given, family)]
    google = acct("google", people[0].work_email, aliases=(alias + G6_D,))
    okta = acct("okta", lookalike + G6_D)
    res = match_accounts([google, okta], people, policy)
    assert res.matches[google.key].rule == "exact_email"
    assert res.unmatched == [okta.key]


@pytest.mark.parametrize(
    "lookalike",
    [
        pytest.param(lookalike + G6_D, marks=g6 if name == "kelvin sign" else (), id=name)
        for name, (_, _, _, lookalike) in G6_LOOKALIKES.items()
    ],
)
def test_g6_rule_2_refuses_a_case_folding_look_alike(lookalike):
    # rule 2 already refuses the ligatures and the long s (NFD keeps them); only the Kelvin sign,
    # which NFD turns into `k` with no accent, gets through
    assert norm_email(lookalike) is None
