"""The held-out v6 world (milestone 3d part 2): resets, same-day repeats, several-question
messages and relayed requests that name nobody (docs/plans/milestone-3d-part-2.md).

Counts, booleans and digests only, so a failure never prints v6 wording. Each value is put in a
variable before it is asserted: pytest would otherwise show a call's arguments, texts included.
No code signal (the role signal, the reset signal) is run on v6 before its final run.
"""

import collections
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meridian_dataset import HOLDOUT_V6_SEED, generate
from meridian_dataset import knowledge as kn
from meridian_dataset.generator import SAME_DAY_LIMIT, USE_EVERY_TEMPLATE, split_for_seed
from meridian_dataset.month import HELD_OUT_MONTH1, TUNING_MONTH, load_templates
from meridian_dataset.names import HELD_OUT_V6_POOL, MESSAGE_POOLS, Part2Pool
from meridian_dataset.overlap import SPLITTERS, pool_texts, repeated_count, shared_count
from meridian_dataset.seal import HELD_OUT_V6_LABELS_SHA256, HELD_OUT_V6_SHA256, pool_digest

POOLS = Path(__file__).resolve().parents[1] / "src/meridian_dataset/pools"
LABELS = POOLS / "held_out_v6_labels.json"
PART2_KEYS = {"normal", "colleague", "unauthenticated", "reset", "several", "repeat"}


def test_v6_and_its_labels_are_unchanged() -> None:
    digest = pool_digest(HELD_OUT_V6_POOL)
    assert digest == HELD_OUT_V6_SHA256
    canonical = json.dumps(
        json.loads(LABELS.read_text("utf-8")), sort_keys=True, separators=(",", ":")
    )
    labels_digest = hashlib.sha256(canonical.encode()).hexdigest()
    assert labels_digest == HELD_OUT_V6_LABELS_SHA256


def test_v6_is_registered_and_sealed_by_its_own_seed() -> None:
    is_part2 = isinstance(HELD_OUT_V6_POOL, Part2Pool)
    assert is_part2
    name = split_for_seed(HOLDOUT_V6_SEED)
    assert name == "held_out_v6"
    registered, every_template = name in MESSAGE_POOLS, name in USE_EVERY_TEMPLATE
    assert (registered, every_template) == (True, True)
    keys = set(json.loads((POOLS / "held_out_v6.json").read_text("utf-8")))
    assert keys == PART2_KEYS


def test_v6_has_the_counts_the_brief_asked_for() -> None:
    pool = HELD_OUT_V6_POOL
    sizes = {
        g: len(getattr(pool, g))
        for g in ("normal", "self_described", "duplicate", "colleague", "unauthenticated")
    }
    assert sizes == {
        "normal": 6,
        "self_described": 0,
        "duplicate": 0,
        "colleague": 18,
        "unauthenticated": 4,
    }
    resets, several = len(pool.reset), len(pool.several)
    assert (resets, several) == (14, 12)
    chain_lengths = [len(chain) for chain in pool.repeat]
    assert chain_lengths == [4, 4, 4, 4, 4]
    texts = pool_texts(pool)
    total, distinct = len(texts), len(set(texts))
    assert (total, distinct) == (74, 74)


def test_v6_labels_match_its_groups() -> None:
    pool = HELD_OUT_V6_POOL
    labels = json.loads(LABELS.read_text("utf-8"))
    keys = set(labels)
    assert keys == PART2_KEYS - {"repeat"}
    lengths_match = all(len(labels[g]) == len(getattr(pool, g)) for g in labels)
    assert lengths_match
    counts = {g: dict(collections.Counter(v)) for g, v in labels.items()}
    assert counts == {
        "normal": {"plain": 6},
        "colleague": {"relayed_unnamed": 10, "reset_for_other": 4, "several_colleague": 4},
        "unauthenticated": {"reset": 4},
        "reset": {"password": 3, "mfa": 4, "unlock": 2, "proof": 2, "with_username": 3},
        "several": {"access": 4, "broken": 4, "howto": 4},
    }
    named = dict(
        collections.Counter(
            label
            for text, label in zip(pool.colleague, labels["colleague"], strict=True)
            if "{target}" in text
        )
    )
    assert named == {"reset_for_other": 2, "several_colleague": 2}


def test_v6_placeholders_keep_their_shapes() -> None:
    pool = HELD_OUT_V6_POOL

    def fields(text: str) -> list[str]:
        return re.findall(r"\{([a-z_]+)\}", text)

    bad_colleague = sum(
        sorted(fields(t)) not in ([], ["target"], ["target", "target_guess"])
        for t in pool.colleague
    )
    claimed = sum(fields(t) == ["claimed_name"] for t in pool.unauthenticated)
    bad_outsider = sum(fields(t) not in ([], ["claimed_name"]) for t in pool.unauthenticated)
    plain = [*pool.normal, *pool.reset, *pool.several, *(m for c in pool.repeat for m in c)]
    braces = sum(("{" in t or "}" in t) for t in plain)
    assert (bad_colleague, claimed, bad_outsider, braces) == (0, 2, 0, 0)


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
def test_no_sentence_repeats_inside_v6(split) -> None:  # type: ignore[no-untyped-def]
    repeated = repeated_count(pool_texts(HELD_OUT_V6_POOL), split)
    assert repeated == 0


def _others() -> dict[str, list[str]]:
    out = {n: pool_texts(p) for n, p in MESSAGE_POOLS.items() if n != "held_out_v6"}
    out["tuning_several"] = json.loads((POOLS / "tuning_several.json").read_text("utf-8"))[
        "several"
    ]
    out["month_tuning"] = [t.text for t in load_templates(TUNING_MONTH)]
    out["month_sealed"] = [t.text for t in load_templates(HELD_OUT_MONTH1)]
    out["howto_tuning"] = [q.text for q in kn.load_question_set(kn.TUNING).questions]
    out["howto_k1"] = [q.text for q in kn.load_question_set(kn.HELD_OUT_K1).questions]
    return out


@pytest.mark.parametrize("split", SPLITTERS, ids=lambda s: s.__name__)
def test_v6_shares_no_sentence_with_any_other_set(split) -> None:  # type: ignore[no-untyped-def]
    mine = pool_texts(HELD_OUT_V6_POOL)
    shared = sum(shared_count(mine, texts, split) for texts in _others().values())
    templates = sum(len(set(mine) & set(texts)) for texts in _others().values())
    assert (shared, templates) == (0, 0)


def test_the_v6_world_uses_every_template_once() -> None:
    world = generate(HOLDOUT_V6_SEED)
    pool_name, tickets = world.message_pool, len(world.tickets)
    assert (pool_name, tickets) == ("held_out_v6", 74)
    scenarios = dict(collections.Counter(e.scenario for e in world.answer_key.entries))
    assert scenarios == {
        "normal": 21,
        "colleague_lookup": 18,
        "unauthenticated": 4,
        "credential_reset": 14,
        "several": 12,
        "same_day_repeat": 5,
    }
    braces = sum(("{" in t.text or "}" in t.text) for t in world.tickets)
    assert braces == 0


def test_each_v6_chain_is_one_sender_on_one_day_with_its_earlier_requests_loaded() -> None:
    world = generate(HOLDOUT_V6_SEED)
    by_time = {t.received_at: t for t in world.tickets}
    by_id = {t.id: t for t in world.tickets}
    last = [e for e in world.answer_key.entries if len(e.prior_same_day_requests) == SAME_DAY_LIMIT]
    well_formed = 0
    for entry in last:
        chain = [*(by_time[s] for s in entry.prior_same_day_requests), by_id[entry.ticket_id]]
        times = [t.received_at for t in chain]
        start, end = (datetime.strptime(times[i], "%Y-%m-%dT%H:%M:%S.000Z") for i in (0, -1))
        priors = [len(world.answer_key.entry(t.id).prior_same_day_requests) for t in chain]
        well_formed += (
            len({t.sender_slack_user_id for t in chain}) == 1
            and times == sorted(times)
            and end - start < timedelta(hours=24)
            and priors == [0, 1, 2, 3]
        )
    assert (len(last), well_formed) == (5, 5)
