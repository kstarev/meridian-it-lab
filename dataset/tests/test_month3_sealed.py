"""held_out_month3: written blind from docs/plans/briefs/month3-brief.md, copied in byte for
byte, and sealed on 2026-11-19, after its pass rules and stop rule (milestone 3f). Every check
prints counts, ids, kinds and languages only: a template's text is kept out of its repr, and no
assertion here holds any wording."""

import re
from collections import Counter

from meridian_dataset.month import (
    DEFAULT_SEEDS,
    EXTRA_TICKETS,
    HELD_OUT_MONTH1,
    HELD_OUT_MONTH2,
    HELD_OUT_MONTH2B,
    HELD_OUT_MONTH3,
    SEALED_SETS,
    SPENT_SETS,
    TUNING_MONTH,
    load_templates,
    month_digest,
    month_for,
)
from meridian_dataset.month.seal import HELD_OUT_MONTH3_SHA256, file_digest

HELD_OUT_MONTH3_MONTH_SHA256 = "9b8633999060203812a41363d085ed9b98df734a70b61f1869cb360924798991"
KINDS = {
    "own_username": 14, "self_described": 6, "someone_else": 8, "relayed": 6,
    "how_to_guide": 24, "how_to_no_guide": 8, "password_reset": 10, "mfa_reset": 6,
    "access_request": 10, "hardware": 20, "app_issue": 18, "incident": 4,
    "prompt_injection": 18, "multi_question": 4, "chitchat": 16,
}  # fmt: skip
EXTRA = {"prompt_injection": 25, "app_issue": 25, "hardware": 25, "chitchat": 25}


def _pieces(text: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[.!?\n]+", text) if len(p.split()) >= 3}


def test_the_seal_holds() -> None:
    assert HELD_OUT_MONTH3 in SEALED_SETS and HELD_OUT_MONTH3 not in SPENT_SETS
    assert file_digest(HELD_OUT_MONTH3) == HELD_OUT_MONTH3_SHA256
    assert DEFAULT_SEEDS[HELD_OUT_MONTH3] == 4813


def test_it_has_the_shape_the_rules_ask_for() -> None:
    templates = load_templates(HELD_OUT_MONTH3)
    assert [t.id for t in templates] == [f"T{n:03d}" for n in range(1, 173)]
    assert dict(Counter(t.kind for t in templates)) == KINDS
    assert dict(Counter(t.lang for t in templates)) == {"en": 120, "de": 18, "es": 17, "fr": 17}
    assert 12 <= sum("{colleague}" in t.text for t in templates) <= 16
    guides = Counter(t.expected_guide for t in templates if t.expected_guide)
    assert len(guides) >= 20
    assert max(v for g, v in guides.items() if g != "KB-017") <= 2 and guides["KB-017"] <= 4
    slow = [t for t in templates if t.slowness_only]
    assert 3 <= len(slow) <= 4
    assert all(t.kind == "how_to_guide" and t.expected_guide == "KB-017" for t in slow)


def test_it_shares_no_sentence_with_any_earlier_month() -> None:
    others: set[str] = set()
    for name in (TUNING_MONTH, HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B):
        for t in load_templates(name):
            others |= _pieces(t.text)
    shared = sum(len(_pieces(t.text) & others) for t in load_templates(HELD_OUT_MONTH3))
    assert shared == 0


def test_the_month_is_the_realistic_mix_plus_the_extra_tickets() -> None:
    assert EXTRA_TICKETS[HELD_OUT_MONTH3] == EXTRA
    month = month_for(HELD_OUT_MONTH3)
    assert len(month.tickets) == 400
    plain = month_for(HELD_OUT_MONTH3, seed=4813)  # the same call: the extras always apply
    assert month_digest(plain) == month_digest(month) == HELD_OUT_MONTH3_MONTH_SHA256
    counts = Counter(t.kind for t in month.tickets)
    assert sum(counts[k] for k in EXTRA) >= 100 + 4  # every extra kind is also in the mix


def test_no_other_set_gets_extra_tickets() -> None:
    assert set(EXTRA_TICKETS) == {HELD_OUT_MONTH3}
