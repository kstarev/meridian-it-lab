"""held_out_month2b: written blind from docs/plans/briefs/held_out_month2b-brief.md, copied in
byte for byte, and sealed on 2026-11-18, after its pass rules (milestone 3e). Every check prints
counts, ids, kinds and languages only; no assertion here holds any wording."""

import re
from collections import Counter

from meridian_dataset.month import (
    DEFAULT_SEEDS,
    HELD_OUT_MONTH1,
    HELD_OUT_MONTH2,
    HELD_OUT_MONTH2B,
    SEALED_SETS,
    TUNING_MONTH,
    load_templates,
    month_digest,
    month_for,
)
from meridian_dataset.month.seal import HELD_OUT_MONTH2B_SHA256, file_digest

HELD_OUT_MONTH2B_MONTH_SHA256 = "55f44c8ff24ffe8b4c8ca648c0c46c1a999f9614cf9fee9741379ccfcd296d0d"
KINDS = {"chitchat": 18, "hardware": 18, "app_issue": 18, "how_to_guide": 18}


def _pieces(text: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[.!?\n]+", text) if len(p.split()) >= 3}


def test_the_seal_holds() -> None:
    assert HELD_OUT_MONTH2B in SEALED_SETS
    assert file_digest(HELD_OUT_MONTH2B) == HELD_OUT_MONTH2B_SHA256
    assert DEFAULT_SEEDS[HELD_OUT_MONTH2B] == 3141


def test_it_has_the_shape_the_rules_ask_for() -> None:
    templates = load_templates(HELD_OUT_MONTH2B)
    assert [t.id for t in templates] == [f"B{n:03d}" for n in range(1, 73)]
    assert dict(Counter(t.kind for t in templates)) == KINDS
    assert dict(Counter(t.lang for t in templates)) == {"en": 54, "de": 6, "es": 6, "fr": 6}
    assert sum("{colleague}" in t.text for t in templates) <= 6
    guides = Counter(t.expected_guide for t in templates if t.expected_guide)
    assert len(guides) >= 14 and max(guides.values()) <= 2


def test_it_shares_no_sentence_with_any_earlier_month() -> None:
    others: set[str] = set()
    for name in (HELD_OUT_MONTH1, HELD_OUT_MONTH2, TUNING_MONTH):
        for t in load_templates(name):
            others |= _pieces(t.text)
    shared = sum(len(_pieces(t.text) & others) for t in load_templates(HELD_OUT_MONTH2B))
    assert shared == 0


def test_the_month_is_pinned() -> None:
    month = month_for(HELD_OUT_MONTH2B)
    assert month.seed == 3141
    kinds = Counter(t.kind for t in month.tickets)
    assert dict(kinds) == {"how_to_guide": 135, "hardware": 75, "app_issue": 60, "chitchat": 30}
    assert len({t.template_id for t in month.tickets}) == 72
    assert month_digest(month) == HELD_OUT_MONTH2B_MONTH_SHA256
