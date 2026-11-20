"""The realistic month's generator (milestone 3c): seeded, pinned, the brief's mix, repeats.

Most tests run on both template sets. The sealed set's wording is never shown: every check
counts violations first and asserts on the count, so a failure prints numbers, never text.
"""

import re
from collections import Counter

import pytest

from meridian_dataset.month import (
    ANSWER_GUIDE,
    ANSWER_USERNAME,
    DEFAULT_SEEDS,
    EXPECTED,
    EXTRA_TICKETS,
    HAND_OFF,
    KINDS,
    LANGS,
    MONTH_SIZE,
    TEMPLATE_SETS,
    TUNING_MONTH,
    Month,
    Template,
    TemplateError,
    build_month,
    build_personas,
    kind_counts,
    load_templates,
    month_digest,
    month_for,
)
from meridian_dataset.month.generator import FAMILIES, KIND_WEIGHTS, OUTSIDE_NOISE
from meridian_dataset.month.templates import PLACEHOLDER, check_templates

# The tuning month at its fixed seed. If this moves, every tuning result made on it moves.
TUNING_MONTH_SHA256 = "edadc0a8bf9c964c7f5ed763acd4a7e697425e0d2d296ef90497fd890579d9f4"
USERNAME_KINDS = ("own_username", "self_described")
# held_out_month2b holds only 4 kinds on purpose (docs/plans/milestone-3e-month2b-rules.md): it
# tests the picker fix, not a realistic month, so the brief's mix and the planted repeat callers
# (own-username and password requests) don't apply to it.
FOCUSED_SETS = frozenset({"held_out_month2b"})


def _realistic(month: Month) -> None:
    if month.template_set in FOCUSED_SETS:
        pytest.skip(f"{month.template_set} is a focused set, not a realistic month")


@pytest.fixture(scope="module", params=TEMPLATE_SETS)
def month(request: pytest.FixtureRequest) -> Month:
    return month_for(request.param)


# --- the tuning templates -------------------------------------------------------------------


def test_tuning_templates_have_the_format_and_mix() -> None:
    templates = load_templates(TUNING_MONTH)
    assert len(templates) >= 120
    assert [t.id for t in templates] == [f"T{i:03d}" for i in range(1, len(templates) + 1)]
    assert set(Counter(t.kind for t in templates)) == set(KINDS)
    langs = Counter(t.lang for t in templates)
    assert set(langs) == set(LANGS)
    assert 0.65 <= langs["en"] / len(templates) <= 0.75
    for lang in ("de", "es", "fr"):
        assert 0.08 <= langs[lang] / len(templates) <= 0.12
    guides = Counter(t.expected_guide for t in templates if t.kind == "how_to_guide")
    assert len(guides) >= 20 and max(guides.values()) <= 2
    assert sum(t.text.count(PLACEHOLDER) for t in templates) >= 8


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "laptop"}, "unknown kind"),
        ({"lang": "it"}, "unknown language"),
        ({"kind": "how_to_guide", "expected_guide": None}, "needs a KB id"),
        ({"expected_guide": "KB-001"}, "only how-to-guide"),
        ({"text": "send me {target}'s login"}, "only placeholder"),
        ({"slowness_only": "yes"}, "true or false"),
        ({"kind": "chitchat", "slowness_only": True}, "small-talk"),
    ],
)
def test_bad_templates_are_rejected_by_id(change: dict, message: str) -> None:
    good = {
        "id": "X1",
        "kind": "own_username",
        "lang": "en",
        "text": "which login is mine",
        "expected_guide": None,
    }
    with pytest.raises(TemplateError, match=message) as error:
        check_templates((Template(**(good | change)),))
    assert "X1" in str(error.value)


def test_template_text_is_never_in_a_repr() -> None:
    template = load_templates(TUNING_MONTH)[0]
    assert template.text not in repr(template)
    ticket = month_for(TUNING_MONTH).tickets[0]
    assert ticket.text not in repr(ticket)


# --- the people -----------------------------------------------------------------------------


def test_personas_are_a_230_person_company_in_14_countries() -> None:
    people = build_personas()
    assert len(people) == 230
    assert len({p.id for p in people}) == len({p.email for p in people}) == 230
    assert len({p.slack_user_id for p in people}) == 230
    assert all(re.fullmatch(r"[a-z]+\.[a-z]+\d*@meridian\.example", p.email) for p in people)
    assert len({p.country for p in people}) == 14
    langs = Counter(p.language for p in people)
    assert langs == {"en": 161, "de": 23, "es": 23, "fr": 23}
    countries = {lang: {p.country for p in people if p.language == lang} for lang in langs}
    assert countries["de"] == {"DE"} and countries["es"] == {"ES", "MX"}
    assert countries["fr"] == {"CA"}
    assert sum(p.department == "IT" for p in people) == 3
    assert build_personas() == people


# --- the month ------------------------------------------------------------------------------


def test_same_seed_same_month_and_another_seed_another() -> None:
    templates = load_templates(TUNING_MONTH)
    first = build_month(templates, seed=11, name=TUNING_MONTH)
    assert month_digest(first) == month_digest(build_month(templates, seed=11, name=TUNING_MONTH))
    assert month_digest(first) != month_digest(build_month(templates, seed=12, name=TUNING_MONTH))


def test_tuning_month_is_pinned() -> None:
    month = month_for(TUNING_MONTH)
    assert month.seed == DEFAULT_SEEDS[TUNING_MONTH]
    assert month_digest(month) == TUNING_MONTH_SHA256


def test_kind_weights_add_up_and_counts_are_exact() -> None:
    assert sum(KIND_WEIGHTS.values()) == 1000 and set(KIND_WEIGHTS) == set(KINDS)
    counts = kind_counts(KINDS, 292)
    assert sum(counts.values()) == 292
    assert kind_counts(["own_username", "hardware"], 10) == {"own_username": 5, "hardware": 5}


def test_the_mix_is_in_the_briefs_range(month: Month) -> None:
    _realistic(month)
    tickets = month.tickets
    # held_out_month3 adds tickets of four kinds on top of the mix (EXTRA_TICKETS).
    extra = sum(EXTRA_TICKETS.get(month.template_set, {}).values())
    assert len(tickets) == month.size == MONTH_SIZE + extra
    username = sum(t.kind in USERNAME_KINDS for t in tickets) / len(tickets)
    assert 0.10 <= username <= 0.15
    hand_off = sum(t.expected == HAND_OFF for t in tickets) / len(tickets)
    assert 0.60 <= hand_off <= 0.80
    assert {t.kind for t in tickets} == set(KINDS)
    used = {t.template_id for t in tickets}
    assert len(used) >= 100
    assert used == {t.id for t in load_templates(month.template_set)}


def test_every_ticket_has_its_answer_key(month: Month) -> None:
    templates = {t.id: t for t in load_templates(month.template_set)}
    bad = 0
    for t in month.tickets:
        template = templates.get(t.template_id)
        bad += template is None or template.kind != t.kind or template.lang != t.lang
        bad += (t.expected, t.reason) != EXPECTED[t.kind]
        if t.expected == ANSWER_GUIDE:
            bad += template is None or t.expected_guide != template.expected_guide
        else:
            bad += t.expected_guide is not None
    assert bad == 0


def test_ticket_ids_run_in_time_order(month: Month) -> None:
    ids = [t.id for t in month.tickets]
    assert ids == [f"MON-{i:03d}" for i in range(1, len(ids) + 1)]
    times = [t.sent_at for t in month.tickets]
    assert times == sorted(times)
    days = {t.local_day for t in month.tickets}
    assert len(days) == 20 and min(days) == "2026-11-02" and max(days) == "2026-11-27"


def test_senders_write_the_templates_language(month: Month) -> None:
    people = {p.id: p for p in month.personas}
    wrong = sum(people[t.persona_id].language != t.lang for t in month.tickets)
    from_it = sum(people[t.persona_id].department == "IT" for t in month.tickets)
    mismatched = sum(
        people[t.persona_id].email != t.sender_email
        or people[t.persona_id].full_name != t.sender_name
        for t in month.tickets
    )
    assert (wrong, from_it, mismatched) == (0, 0, 0)


def test_repeat_callers_are_planted_and_marked(month: Month) -> None:
    _realistic(month)
    by_id = {t.id: t for t in month.tickets}
    repeats = [t for t in month.tickets if t.repeat]
    assert Counter(t.kind for t in repeats) == {"own_username": 5, "password_reset": 3}
    bad = 0
    for t in repeats:
        first = by_id.get(t.repeat_of or "")
        bad += first is None or first.repeat
        if first is None:
            continue
        bad += first.persona_id != t.persona_id or first.local_day != t.local_day
        bad += not first.sent_at < t.sent_at
        bad += FAMILIES.get(first.kind) != FAMILIES.get(t.kind)
    assert bad == 0
    assert sum(t.repeat_of is not None for t in month.tickets) == len(repeats)
    # Username repeats are expected to be answered (the ladder comes later); a repeated
    # password reset still goes to a person.
    assert {t.expected for t in repeats if t.kind == "own_username"} == {ANSWER_USERNAME}
    assert {t.expected for t in repeats if t.kind == "password_reset"} == {HAND_OFF}


def test_no_unplanted_same_day_repeats(month: Month) -> None:
    pairs = Counter(
        (t.persona_id, t.local_day, FAMILIES[t.kind]) for t in month.tickets if t.kind in FAMILIES
    )
    assert sum(n - 1 for n in pairs.values() if n > 1) == sum(t.repeat for t in month.tickets)


def test_colleague_is_another_persons_full_name(month: Month) -> None:
    templates = {t.id: t for t in load_templates(month.template_set)}
    people = {p.id: p for p in month.personas}
    braces = sum("{" in t.text or "}" in t.text for t in month.tickets)
    bad = 0
    named = 0
    for t in month.tickets:
        has_placeholder = PLACEHOLDER in templates[t.template_id].text
        bad += has_placeholder != (t.colleague_id is not None)
        if t.colleague_id is not None:
            named += 1
            bad += t.colleague_id == t.persona_id
            bad += people[t.colleague_id].full_name not in t.text
    assert (braces, bad) == (0, 0)
    assert named >= 8


def test_slack_noise_is_light_and_never_inside_an_injection(month: Month) -> None:
    templates = {t.id: t for t in load_templates(month.template_set)}
    people = {p.id: p for p in month.personas}
    noisy = sum(bool(t.noise) for t in month.tickets) / len(month.tickets)
    assert 0.15 <= noisy <= 0.35
    assert {n for t in month.tickets for n in t.noise} == {"greeting", "emoji", "typo"}
    injections = [t for t in month.tickets if t.kind == "prompt_injection"]
    touched = 0
    for t in injections:
        touched += any(n not in OUTSIDE_NOISE for n in t.noise)
        own_words = templates[t.template_id].text
        if t.colleague_id is not None:
            own_words = own_words.replace(PLACEHOLDER, people[t.colleague_id].full_name)
        touched += own_words not in t.text
    assert touched == 0


def test_a_typo_changes_the_text(month: Month) -> None:
    templates = {t.id: t for t in load_templates(month.template_set)}
    unchanged = sum(
        1
        for t in month.tickets
        if t.noise == ("typo",)
        and t.colleague_id is None
        and t.text == templates[t.template_id].text
    )
    assert unchanged == 0
