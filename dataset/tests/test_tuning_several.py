"""R25's several-request tuning messages (2026-11-16): count-only checks against the sealed sets.

No sealed wording is printed or read by eye: the tests count shared sentences only."""

import json
import re

from meridian_dataset import knowledge as kn
from meridian_dataset.month import HELD_OUT_MONTH1, load_templates
from meridian_dataset.names import MESSAGE_POOLS

PATH = kn.HERE.parent / "pools" / "tuning_several.json"


def _several() -> list[str]:
    return json.loads(PATH.read_text(encoding="utf-8"))["several"]


def _sentences(text: str) -> set[str]:
    """Lower-cased sentences of 3 words or more, placeholders removed."""
    text = re.sub(r"\{[a-z_]+\}", " ", text.lower())
    return {" ".join(s.split()) for s in re.split(r"[.!?\n]+", text) if len(s.split()) >= 3}


def test_the_several_request_messages() -> None:
    texts = _several()
    assert len(texts) == 12 and len(set(texts)) == 12
    assert not any("{" in t for t in texts)


def _shared_with(sealed_texts: list[str]) -> int:
    sealed = set().union(*(_sentences(t) for t in sealed_texts))
    return sum(len(_sentences(t) & sealed) for t in _several())


def test_they_share_no_sentence_with_held_out_v5() -> None:
    pool = MESSAGE_POOLS["held_out_v5"]
    texts = [*pool.normal, *pool.self_described, *pool.colleague, *pool.unauthenticated]
    assert _shared_with(texts) == 0


def test_they_share_no_sentence_with_the_sealed_month() -> None:
    assert _shared_with([t.text for t in load_templates(HELD_OUT_MONTH1)]) == 0


def test_they_share_no_sentence_with_the_sealed_how_to_test() -> None:
    sealed = kn.load_question_set(kn.HELD_OUT_K1)
    assert _shared_with([q.text for q in sealed.questions]) == 0


def test_milestone_4s_r24_and_r25_tuning_questions_share_no_sentence_with_the_sealed_month() -> (
    None
):
    """The 20 how-to tuning questions added for R24 and R25 tune the picker, and the sealed
    month also runs the picker."""
    tuning = kn.load_question_set(kn.TUNING)
    new = [q.text for q in tuning.questions if q.reason in ("broken", "several")]
    assert len(new) == 20
    sealed = set().union(*(_sentences(t.text) for t in load_templates(HELD_OUT_MONTH1)))
    assert sum(len(_sentences(t) & sealed) for t in new) == 0
