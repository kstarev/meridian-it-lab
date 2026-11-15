"""The blind month templates are sealed (milestone 3c). Count-only: nothing here shows text.

held_out_month1.json was written blind by a helper that never saw tier0's code and copied in
byte for byte. These tests check its digest, its shape against the counts the helper
reported, and that the tuning templates share no sentence with it. Every assertion compares
counts, ids, kinds, languages or booleans, so a failure prints numbers, never wording.
"""

import re
from collections import Counter

from meridian_dataset.month import (
    DEFAULT_SEEDS,
    HELD_OUT_MONTH1,
    SEALED_SETS,
    TUNING_MONTH,
    load_templates,
    month_digest,
    month_for,
)
from meridian_dataset.month.seal import HELD_OUT_MONTH1_SHA256, file_digest
from meridian_dataset.month.templates import PLACEHOLDER

# The sealed set's month at its fixed seed, pinned by digest only.
HELD_OUT_MONTH1_MONTH_SHA256 = "70e47d849f45f600dd79d5bbd2ef15ace92726757aa6886f18e9b1e4c0f27906"

KNOWN_KINDS = {
    "own_username": 14,
    "self_described": 6,
    "someone_else": 8,
    "relayed": 6,
    "how_to_guide": 24,
    "how_to_no_guide": 8,
    "password_reset": 10,
    "mfa_reset": 6,
    "access_request": 10,
    "hardware": 8,
    "app_issue": 6,
    "incident": 4,
    "prompt_injection": 6,
    "multi_question": 4,
    "chitchat": 4,
}


def test_the_seal_holds() -> None:
    assert HELD_OUT_MONTH1 in SEALED_SETS
    assert file_digest(HELD_OUT_MONTH1) == HELD_OUT_MONTH1_SHA256


def test_the_sealed_set_has_the_reported_shape() -> None:
    templates = load_templates(HELD_OUT_MONTH1)
    assert len(templates) == 124
    assert [t.id for t in templates] == [f"M{i:03d}" for i in range(1, 125)]
    assert dict(Counter(t.kind for t in templates)) == KNOWN_KINDS
    assert dict(Counter(t.lang for t in templates)) == {"en": 84, "de": 14, "es": 13, "fr": 13}
    guides = Counter(t.expected_guide for t in templates if t.kind == "how_to_guide")
    assert (len(guides), max(guides.values())) == (22, 2)
    assert sum(t.expected_guide is not None for t in templates if t.kind != "how_to_guide") == 0
    assert sum(t.text.count(PLACEHOLDER) for t in templates) == 12
    other_braces = sum(t.text.replace(PLACEHOLDER, "").count("{") for t in templates)
    assert other_braces == 0


def test_the_sealed_month_is_pinned() -> None:
    month = month_for(HELD_OUT_MONTH1)
    assert month.seed == DEFAULT_SEEDS[HELD_OUT_MONTH1]
    assert month_digest(month) == HELD_OUT_MONTH1_MONTH_SHA256


def _pieces(text: str) -> set[str]:
    """Sentences: split on . ! ? and newlines, lower-cased, pieces under 3 words ignored."""
    out = set()
    for piece in re.split(r"[.!?\n]", text.lower()):
        words = piece.split()
        if len(words) >= 3:
            out.add(" ".join(words))
    return out


def test_tuning_templates_share_no_sentence_with_the_sealed_set() -> None:
    sealed: set[str] = set()
    for t in load_templates(HELD_OUT_MONTH1):
        sealed |= _pieces(t.text)
    shared = sum(len(_pieces(t.text) & sealed) for t in load_templates(TUNING_MONTH))
    assert shared == 0
