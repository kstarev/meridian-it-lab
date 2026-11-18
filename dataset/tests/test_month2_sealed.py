"""held_out_month2: written blind from docs/plans/briefs/held_out_month2-brief.md, copied in byte
for byte, and sealed on 2026-11-18, after its pass rules (milestone 3e). Every check prints
counts, ids, kinds and languages only: a template's text is kept out of its repr, and no
assertion here holds any wording."""

import re
from collections import Counter

from meridian_dataset.month import (
    DEFAULT_SEEDS,
    HELD_OUT_MONTH1,
    HELD_OUT_MONTH2,
    SEALED_SETS,
    TUNING_MONTH,
    load_templates,
    month_digest,
    month_for,
)
from meridian_dataset.month.seal import HELD_OUT_MONTH2_SHA256, file_digest

HELD_OUT_MONTH2_MONTH_SHA256 = "12e813d8e061124f33b82172c9b28c2a2bdd9a1d1f011c963519a4aa07f1e041"
KINDS = {
    "own_username": 14, "self_described": 6, "someone_else": 8, "relayed": 6,
    "how_to_guide": 24, "how_to_no_guide": 8, "password_reset": 10, "mfa_reset": 6,
    "access_request": 10, "hardware": 8, "app_issue": 6, "incident": 4,
    "prompt_injection": 6, "multi_question": 4, "chitchat": 4,
}  # fmt: skip


def _pieces(text: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[.!?\n]+", text) if len(p.split()) >= 3}


def test_the_seal_holds() -> None:
    assert HELD_OUT_MONTH2 in SEALED_SETS
    assert file_digest(HELD_OUT_MONTH2) == HELD_OUT_MONTH2_SHA256
    assert DEFAULT_SEEDS[HELD_OUT_MONTH2] == 2718


def test_it_has_the_shape_the_rules_ask_for() -> None:
    templates = load_templates(HELD_OUT_MONTH2)
    assert len(templates) == 124
    assert [t.id for t in templates] == [f"N{n:03d}" for n in range(1, 125)]
    assert dict(Counter(t.kind for t in templates)) == KINDS
    assert dict(Counter(t.lang for t in templates)) == {"en": 87, "de": 13, "es": 12, "fr": 12}
    assert sum("{colleague}" in t.text for t in templates) == 12
    guides = Counter(t.expected_guide for t in templates if t.expected_guide)
    assert len(guides) >= 20 and max(guides.values()) <= 2


def test_it_shares_no_sentence_with_month1_or_the_tuning_month() -> None:
    others: set[str] = set()
    for name in (HELD_OUT_MONTH1, TUNING_MONTH):
        for t in load_templates(name):
            others |= _pieces(t.text)
    shared = sum(len(_pieces(t.text) & others) for t in load_templates(HELD_OUT_MONTH2))
    assert shared == 0


def test_the_month_is_pinned() -> None:
    month = month_for(HELD_OUT_MONTH2)
    assert month.seed == 2718
    assert len(month.tickets) == 300
    assert len({t.template_id for t in month.tickets}) == 124
    assert month_digest(month) == HELD_OUT_MONTH2_MONTH_SHA256
