"""Tuning and held-out messages never share wording (ADR-013)."""

import re

import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, generate
from meridian_dataset.generator import split_for_seed
from meridian_dataset.names import HELD_OUT_POOL, MESSAGE_POOLS, TUNING_POOL, MessagePool

SCENARIOS = ("normal", "duplicate", "colleague", "unauthenticated")


def _templates(pool: MessagePool) -> list[str]:
    return [t for scenario in SCENARIOS for t in getattr(pool, scenario)]


def _sentences(text: str) -> set[str]:
    """Lower-case sentences with placeholders and punctuation removed."""
    text = re.sub(r"\{[a-z_]+\}", " ", text.lower())
    parts = re.split(r"(?<=[.?!])\s+", text)
    return {" ".join(re.findall(r"[a-z0-9']+", p)) for p in parts} - {""}


def test_every_scenario_has_wording_in_both_pools() -> None:
    for scenario in SCENARIOS:
        assert getattr(TUNING_POOL, scenario), scenario
        assert getattr(HELD_OUT_POOL, scenario), scenario


def test_no_template_is_shared() -> None:
    assert not set(_templates(TUNING_POOL)) & set(_templates(HELD_OUT_POOL))


def test_no_sentence_is_shared() -> None:
    tuning = set().union(*(_sentences(t) for t in _templates(TUNING_POOL)))
    held_out = set().union(*(_sentences(t) for t in _templates(HELD_OUT_POOL)))
    assert tuning and held_out
    assert not tuning & held_out


def test_no_generated_ticket_text_is_shared() -> None:
    tuning_texts = {t.text for seed in (DEFAULT_SEED, *range(50)) for t in generate(seed).tickets}
    held_out_texts = {t.text for t in generate(HOLDOUT_SEED).tickets}
    assert len(held_out_texts) == 6
    assert not tuning_texts & held_out_texts


def test_pool_follows_the_seed() -> None:
    assert split_for_seed(HOLDOUT_SEED) == "held_out"
    assert split_for_seed(DEFAULT_SEED) == "tuning"
    assert generate(HOLDOUT_SEED).message_pool == "held_out"
    assert generate(DEFAULT_SEED).message_pool == "tuning"
    assert set(MESSAGE_POOLS) == {"tuning", "held_out"}


def test_held_out_templates_keep_the_scenario_shape() -> None:
    assert all("{other_email}" in t for t in HELD_OUT_POOL.duplicate)
    assert all("{target}" in t for t in HELD_OUT_POOL.colleague)
    assert all("Alex Kim" in t for t in HELD_OUT_POOL.unauthenticated)
    assert all("{" not in t for t in (*HELD_OUT_POOL.normal, *HELD_OUT_POOL.unauthenticated))


def test_unknown_split_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown split"):
        generate(DEFAULT_SEED, split="test")
