"""Milestone 4's data (ADR-035): the runbooks, the resolved tickets and the sealed how-to
test. Every check on the sealed set is a count, so a failure never prints its wording."""

import collections
import dataclasses
import hashlib
import json
import re

from meridian_dataset import knowledge as kn
from meridian_dataset.knowledge import seal

GUIDES = kn.load_runbooks()


def test_thirty_approved_runbooks_with_real_steps() -> None:
    assert [g.id for g in GUIDES] == [f"KB-{n:03d}" for n in range(1, 31)]
    assert len({g.slug for g in GUIDES}) == len({g.title for g in GUIDES}) == 30
    for g in GUIDES:
        assert g.approved
        assert 4 <= len(g.steps) <= 7, g.id
        assert g.summary.endswith(".") and len(g.summary) < 160, g.id
        assert g.link == f"https://memory.meridian.example/runbooks/{g.slug}.md"
        md = kn.render_markdown(g)
        assert md.startswith(f"# {g.title}\n") and all(step in md for step in g.steps)


def test_the_runbooks_hold_only_fake_addresses_and_no_secrets() -> None:
    text = (kn.HERE / "runbooks.json").read_text(encoding="utf-8")
    assert not re.search(r"https?://", text)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", text)
    assert not re.search(r"(?i)api[_-]?key|bearer |-----BEGIN", text)


def test_a_draft_runbook_is_never_loaded(tmp_path, monkeypatch) -> None:
    raw = json.loads((kn.HERE / "runbooks.json").read_text(encoding="utf-8"))
    raw["runbooks"][0]["approved"] = False
    (tmp_path / "runbooks.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(kn, "HERE", tmp_path)
    assert kn.load_runbooks()[0].id == "KB-002"
    assert len(kn.load_runbooks()) == 29


def test_the_resolved_tickets_are_seeded_and_cover_every_guide() -> None:
    tickets = kn.resolved_tickets()
    assert tickets == kn.resolved_tickets()
    assert tickets != kn.resolved_tickets(seed=1)
    assert len(tickets) == 235
    groups = collections.Counter(t.question.group for t in tickets)
    assert groups == {
        "guide": 120,
        "no_guide": 30,
        "must_hand_off": 51,
        "injected_question": 20,
        "poisoned_guide": 14,
    }
    per_guide = collections.Counter(
        t.question.expected_guide for t in tickets if t.question.group == "guide"
    )
    assert set(per_guide) == {g.id for g in GUIDES} and set(per_guide.values()) == {4}
    for t in tickets:
        assert t.resolution == ("guide" if t.question.expected_guide else "handed_off")
        assert t.opened_at < t.resolved_at <= "2026-11-02T14:00:00Z"
    blob = json.dumps([dataclasses.asdict(t) for t in tickets], sort_keys=True, ensure_ascii=False)
    # Pinned like the tier0 worlds: a change to the generator or the templates shows up here.
    assert hashlib.sha256(blob.encode()).hexdigest() == RESOLVED_TICKETS_SHA256


# Re-pinned on 2026-11-16: 20 tuning templates added for rulings R24 and R25.
RESOLVED_TICKETS_SHA256 = "28bfcf403471a38f76313e20285d0f0ff56a6e6af1dfaf8172f9ffb22a34cc58"


def test_the_tuning_set_is_well_formed() -> None:
    tuning = kn.load_question_set(kn.TUNING)
    assert kn.problems(tuning, GUIDES) == []
    assert len({q.text for q in tuning.questions}) == len(tuning.questions)
    planted = collections.Counter(p for g in tuning.poisoned_guides for p in g.planted)
    assert planted == {
        "summary": 3,
        "steps_start": 1,
        "steps_middle": 1,
        "steps_end": 1,
        "steps_quoted": 1,
    }


# The sealed how-to test: counts and booleans only.


def test_the_sealed_how_to_test_is_unchanged() -> None:
    assert seal.question_set_digest(kn.HELD_OUT_K1) == seal.HELD_OUT_K1_SHA256


def test_the_sealed_how_to_test_has_the_shape_its_brief_asked_for() -> None:
    sealed = kn.load_question_set(kn.HELD_OUT_K1)
    assert kn.problems(sealed, GUIDES) == []
    questions = sealed.questions
    assert len(questions) == 88
    assert collections.Counter(q.group for q in questions) == {
        "guide": 34,
        "no_guide": 12,
        "must_hand_off": 14,
        "injected_question": 12,
        "poisoned_guide": 16,
    }
    reasons = collections.Counter(q.reason for q in questions if q.group == "must_hand_off")
    assert reasons == {"account_action": 6, "someone_else": 5, "incident": 3}
    assert collections.Counter(q.lang for q in questions) == {"en": 61, "de": 9, "es": 9, "fr": 9}
    per_guide = collections.Counter(q.expected_guide for q in questions if q.group == "guide")
    assert len(per_guide) == 30 and max(per_guide.values()) == 2
    injected = [q for q in questions if q.group == "injected_question"]
    assert sum(q.expected_guide is None for q in injected) == 6
    assert not any(q.attack_guide == q.expected_guide for q in injected if q.attack_guide)


def test_the_sealed_poisoned_guides_plant_instructions_in_every_position() -> None:
    sealed = kn.load_question_set(kn.HELD_OUT_K1)
    assert len(sealed.poisoned_guides) == 8
    planted = collections.Counter(p for g in sealed.poisoned_guides for p in g.planted)
    assert planted == {
        "summary": 4,
        "steps_start": 1,
        "steps_middle": 1,
        "steps_end": 1,
        "steps_quoted": 1,
    }
    assert all(5 <= len(g.steps) <= 7 for g in sealed.poisoned_guides)
    for g in sealed.poisoned_guides:
        mine = [q for q in sealed.questions if q.poisoned == g.id]
        assert sorted(q.expected_guide is None for q in mine) == [False, True], g.id


def _sentences(text: str) -> set[str]:
    return {s.strip().lower() for s in re.split(r"[.!?\n]+", text) if len(s.split()) >= 3}


def test_tuning_and_the_sealed_set_share_one_short_sentence_and_no_more() -> None:
    """Count-only. One sealed question shares a 4-word sentence with a tuning question; the
    set was sealed as its helper wrote it, and this pins the count."""
    tuning = kn.load_question_set(kn.TUNING)
    sealed = kn.load_question_set(kn.HELD_OUT_K1)
    tuning_sentences = set().union(*(_sentences(q.text) for q in tuning.questions))
    shared = sum(bool(_sentences(q.text) & tuning_sentences) for q in sealed.questions)
    assert shared == 1, f"{shared} sealed question(s) share a sentence with tuning"
    assert not {q.text for q in tuning.questions} & {q.text for q in sealed.questions}
    guide_text = [" . ".join((g.summary, *g.steps)) for g in tuning.poisoned_guides]
    tuning_guides = set().union(*(_sentences(t) for t in guide_text))
    assert not any(
        _sentences(" . ".join((g.summary, *g.steps))) & tuning_guides
        for g in sealed.poisoned_guides
    )
