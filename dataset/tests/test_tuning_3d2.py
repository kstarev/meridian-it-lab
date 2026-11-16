"""Milestone 3d part 2's tuning messages and tuning world (docs/plans/milestone-3d-part-2.md).

Checks against other sets are counts only: no sealed wording is printed or read by eye.
"""

import collections
import json
from datetime import datetime, timedelta

import pytest

from meridian_dataset import TUNING_3D2_SEED, generate
from meridian_dataset import knowledge as kn
from meridian_dataset.generator import SAME_DAY_LIMIT, split_for_seed
from meridian_dataset.month import HELD_OUT_MONTH1, TUNING_MONTH, load_templates
from meridian_dataset.names import MESSAGE_POOLS, TUNING_3D2_POOL, Part2Pool
from meridian_dataset.overlap import SPLITTERS, pool_texts, repeated_count, shared_count

POOLS = kn.HERE.parent / "pools"
LABELS = json.loads((POOLS / "tuning_3d2_labels.json").read_text("utf-8"))
SEVERAL_R25 = json.loads((POOLS / "tuning_several.json").read_text("utf-8"))["several"]
MINE = pool_texts(TUNING_3D2_POOL)


def test_the_groups_and_their_sizes() -> None:
    pool = TUNING_3D2_POOL
    assert isinstance(pool, Part2Pool)
    sizes = {
        g: len(getattr(pool, g))
        for g in ("normal", "self_described", "duplicate", "colleague", "unauthenticated")
    }
    assert sizes == {
        "normal": 6,
        "self_described": 0,
        "duplicate": 3,
        "colleague": 16,
        "unauthenticated": 4,
    }
    assert (len(pool.reset), len(pool.several)) == (14, 12)
    assert [len(chain) for chain in pool.repeat] == [4, 4, 4, 4]
    assert len(MINE) == 71 and len(set(MINE)) == 71


def test_the_labels_match_the_groups() -> None:
    pool = TUNING_3D2_POOL
    assert set(LABELS) == {
        "normal",
        "duplicate",
        "colleague",
        "unauthenticated",
        "reset",
        "several",
    }
    for group, labels in LABELS.items():
        assert len(labels) == len(getattr(pool, group)), group
    counts = {g: dict(collections.Counter(v)) for g, v in LABELS.items()}
    assert counts["colleague"] == {
        "relayed_unnamed": 8,
        "reset_for_other": 4,
        "several_colleague": 4,
    }
    assert counts["reset"] == {"password": 3, "mfa": 4, "unlock": 2, "proof": 2, "with_username": 3}
    assert counts["several"] == {"access": 4, "broken": 4, "howto": 4}
    assert counts["unauthenticated"] == {"reset": 4}
    named = collections.Counter(
        label
        for text, label in zip(pool.colleague, LABELS["colleague"], strict=True)
        if "{target}" in text
    )
    assert named == {"reset_for_other": 2, "several_colleague": 2}


def test_part_2s_groups_have_no_placeholders() -> None:
    pool = TUNING_3D2_POOL
    texts = [*pool.reset, *pool.several, *(m for chain in pool.repeat for m in chain)]
    assert not any("{" in t or "}" in t for t in texts)


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
def test_no_sentence_repeats_inside_the_pool(split) -> None:  # type: ignore[no-untyped-def]
    assert repeated_count(MINE, split) == 0


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
@pytest.mark.parametrize("name", sorted(set(MESSAGE_POOLS) - {"tuning_3d2"}))
def test_no_sentence_is_shared_with_another_pool(name: str, split) -> None:  # type: ignore[no-untyped-def]
    assert shared_count(MINE, pool_texts(MESSAGE_POOLS[name]), split) == 0


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
def test_no_sentence_is_shared_with_r25s_messages_or_the_months(split) -> None:  # type: ignore[no-untyped-def]
    assert shared_count(MINE, SEVERAL_R25, split) == 0
    for name in (TUNING_MONTH, HELD_OUT_MONTH1):
        assert shared_count(MINE, [t.text for t in load_templates(name)], split) == 0, name


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
def test_no_sentence_is_shared_with_the_how_to_questions(split) -> None:  # type: ignore[no-untyped-def]
    for name in (kn.TUNING, kn.HELD_OUT_K1):
        questions = [q.text for q in kn.load_question_set(name).questions]
        assert shared_count(MINE, questions, split) == 0, name


# --- the tuning world ------------------------------------------------------------------


def test_the_tuning_world_uses_every_template_once() -> None:
    assert split_for_seed(TUNING_3D2_SEED) == "tuning_3d2"
    world = generate(TUNING_3D2_SEED)
    assert world.message_pool == "tuning_3d2"
    assert len(world.tickets) == 71
    assert sorted(t.text for t in world.tickets if "{" not in t.text) == sorted(
        t.text for t in world.tickets
    )
    scenarios = collections.Counter(e.scenario for e in world.answer_key.entries)
    assert scenarios == {
        "normal": 6 + 12,
        "duplicate_name": 3,
        "colleague_lookup": 16,
        "unauthenticated": 4,
        "credential_reset": 14,
        "several": 12,
        "same_day_repeat": 4,
    }
    resets = [e for e in world.answer_key.entries if e.scenario == "credential_reset"]
    assert all(e.tier == "C" and e.allowed_tools == () for e in resets)
    several = [e for e in world.answer_key.entries if e.scenario == "several"]
    assert all(len(e.split_facts) == 1 and "@" in e.split_facts[0] for e in several)


def _time(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.000Z")


def test_each_chain_is_one_sender_on_one_day_with_its_earlier_requests_loaded() -> None:
    world = generate(TUNING_3D2_SEED)
    by_time = {t.received_at: t for t in world.tickets}
    assert len(by_time) == len(world.tickets)
    last = [e for e in world.answer_key.entries if len(e.prior_same_day_requests) == SAME_DAY_LIMIT]
    assert len(last) == 4
    in_chains = 0
    for final in last:
        chain = [*(by_time[s] for s in final.prior_same_day_requests), _ticket(world, final)]
        assert len({t.sender_slack_user_id for t in chain}) == 1
        times = [t.received_at for t in chain]
        assert times == sorted(times)
        assert _time(times[-1]) - _time(times[0]) < timedelta(hours=24)
        for position, ticket in enumerate(chain):
            entry = world.answer_key.entry(ticket.id)
            assert entry.prior_same_day_requests == tuple(times[:position])
            expected = "normal" if position < SAME_DAY_LIMIT else "same_day_repeat"
            assert entry.scenario == expected
            in_chains += 1
    assert in_chains == 16
    loaded = [e for e in world.answer_key.entries if e.prior_same_day_requests]
    assert len(loaded) == 12


def _ticket(world, entry):  # type: ignore[no-untyped-def]
    return next(t for t in world.tickets if t.id == entry.ticket_id)


def test_the_answer_key_writes_the_new_fields_only_when_set(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from meridian_dataset import write_dataset

    paths = write_dataset(generate(TUNING_3D2_SEED), tmp_path)
    entries = json.loads(paths["answer_key.json"].read_text("utf-8"))["entries"]
    with_prior = [e for e in entries if "prior_same_day_requests" in e]
    assert len(with_prior) == 4 * 3  # every chain request after the first
    assert sum("split_facts" in e for e in entries) == 12
