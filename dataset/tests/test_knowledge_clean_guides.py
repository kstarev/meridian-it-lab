"""The guide screen fix's guide sets (docs/plans/milestone-4-guide-screen-fix.md): the 12 clean
tuning guides, and `held_out_g2`, registered before it is written.

The checks against sealed sets count shared sentences only, so a failure never prints sealed
wording. Nothing here reads `held_out_g2` before its seal commit adds it."""

import collections
import re

import pytest

from meridian_dataset import knowledge as kn
from meridian_dataset.knowledge import seal
from meridian_dataset.month import HELD_OUT_MONTH1, load_templates
from meridian_dataset.names import MESSAGE_POOLS

GUIDES = kn.load_runbooks()
TUNING = kn.load_question_set(kn.TUNING)
G2_PATH = kn.POOLS / f"{kn.HELD_OUT_G2}.json"


def _sentences(text: str) -> set[str]:
    """Lower-cased sentences of 3 words or more, split on . ! ? and newlines."""
    return {" ".join(s.split()) for s in re.split(r"[.!?\n]+", text.lower()) if len(s.split()) >= 3}


def _guide_text(g: kn.CleanGuide | kn.PoisonedGuide) -> str:
    return "\n".join((g.title, g.summary, *g.steps))


def _shared(texts: list[str], other: list[str]) -> int:
    """How many sentences of `texts` appear in `other`: a count, never the sentences."""
    theirs = set().union(*(_sentences(t) for t in other))
    return sum(len(_sentences(t) & theirs) for t in texts)


CLEAN = [_guide_text(g) for g in TUNING.clean_guides]


def test_twelve_clean_tuning_guides_with_commands_settings_and_quoted_errors() -> None:
    clean = TUNING.clean_guides
    assert [g.id for g in clean] == [f"CT-{n}" for n in range(1, 13)]
    assert kn.problems(TUNING, GUIDES) == []
    assert all(g.quoted for g in clean)
    assert all("`" in _guide_text(g) for g in clean)  # each holds a command or a setting
    assert sum("```" in _guide_text(g) for g in clean) >= 4  # fenced blocks, not only inline
    assert len({g.title for g in clean}) == 12
    assert not {g.title for g in clean} & {g.title for g in GUIDES}
    for g in clean:
        assert 4 <= len(g.steps) <= 7, g.id
        assert g.summary.endswith(".") and len(g.summary) < 160, g.id


def test_the_clean_tuning_guides_use_only_meridian_addresses() -> None:
    text = "\n".join(CLEAN)
    links = re.findall(r"https?://([^/\s`\"')]+)", text)
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text)
    assert links and emails
    assert all(d == "meridian.example" or d.endswith(".meridian.example") for d in links + emails)


def test_the_clean_tuning_guides_copy_no_runbook_or_tuning_poisoned_guide() -> None:
    runbooks = ["\n".join((g.title, g.summary, *g.steps)) for g in GUIDES]
    poisoned = [_guide_text(g) for g in TUNING.poisoned_guides]
    assert _shared(CLEAN, runbooks + poisoned) == 0


# Count-only, against every sealed set with wording in reach of these tests: the sealed message
# pool (v5), the sealed month, the sealed how-to test, and held_out_g2 once its seal commit adds
# it. Access's red-team set is stored compressed and unsealed by its own name only; it is about
# access requests, and no knowledge test reads it.


def test_they_share_no_sentence_with_held_out_v5() -> None:
    pool = MESSAGE_POOLS["held_out_v5"]
    texts = [*pool.normal, *pool.self_described, *pool.colleague, *pool.unauthenticated]
    assert _shared(CLEAN, texts) == 0


def test_they_share_no_sentence_with_the_sealed_month() -> None:
    assert _shared(CLEAN, [t.text for t in load_templates(HELD_OUT_MONTH1)]) == 0


def test_they_share_no_sentence_with_the_sealed_how_to_test() -> None:
    sealed = kn.load_question_set(kn.HELD_OUT_K1)
    texts = [q.text for q in sealed.questions] + [_guide_text(g) for g in sealed.poisoned_guides]
    assert _shared(CLEAN, texts) == 0


@pytest.mark.skipif(not G2_PATH.exists(), reason="held_out_g2 is not written yet")
def test_they_share_no_sentence_with_held_out_g2() -> None:
    sealed = kn.load_question_set(kn.HELD_OUT_G2)
    texts = [_guide_text(g) for g in (*sealed.poisoned_guides, *sealed.clean_guides)]
    tuning = [*CLEAN, *(_guide_text(g) for g in TUNING.poisoned_guides)]
    assert _shared(tuning, texts) == 0


# held_out_g2: registered now, written blind later. Its seal commit adds the file and its digest
# in `seal.py` together; these tests then check both, by counts only.


def test_held_out_g2_is_registered_and_its_file_and_seal_come_together() -> None:
    assert kn.HELD_OUT_G2 in kn.ALL_SETS and kn.HELD_OUT_G2 not in kn.QUESTION_SETS
    assert kn.is_written(kn.HELD_OUT_G2) == G2_PATH.exists()
    # A file without its seal, or a seal without its file, fails here.
    assert (seal.HELD_OUT_G2_SHA256 is None) == (not G2_PATH.exists())


def test_an_unwritten_guide_set_is_refused_by_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kn, "POOLS", tmp_path)
    assert not kn.is_written(kn.HELD_OUT_G2)
    with pytest.raises(FileNotFoundError, match="not written yet"):
        kn.load_question_set(kn.HELD_OUT_G2)
    with pytest.raises(ValueError):
        kn.load_question_set("held_out_g3")


@pytest.mark.skipif(not G2_PATH.exists(), reason="held_out_g2 is not written yet")
def test_the_sealed_guide_set_is_unchanged() -> None:
    assert seal.question_set_digest(kn.HELD_OUT_G2) == seal.HELD_OUT_G2_SHA256


@pytest.mark.skipif(not G2_PATH.exists(), reason="held_out_g2 is not written yet")
def test_the_sealed_guide_set_has_the_shape_the_plan_asks_for() -> None:
    """Counts, ids and booleans only. The shape is the plan's; the brief must match it."""
    sealed = kn.load_question_set(kn.HELD_OUT_G2)
    assert kn.problems(sealed, GUIDES) == []
    assert sealed.questions == ()
    poisoned, clean = sealed.poisoned_guides, sealed.clean_guides
    assert [g.id for g in poisoned] == [f"PG-{n:02d}" for n in range(1, 25)]
    assert [g.id for g in clean] == [f"CG-{n:02d}" for n in range(1, 61)]
    assert all(len(g.planted) == 1 for g in poisoned)
    assert collections.Counter(g.planted[0] for g in poisoned) == {
        "steps_quoted": 12,
        "summary": 3,
        "steps_start": 3,
        "steps_middle": 3,
        "steps_end": 3,
    }
    assert sum(g.quoted for g in clean) == 30
