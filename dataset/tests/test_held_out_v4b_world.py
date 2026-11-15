"""The held-out v4b world (ADR-038): short self-descriptions and unnamed colleague requests only.

Assertions are counts and booleans only, so a failure never prints v4b wording.
"""

import pytest

from meridian_dataset import HOLDOUT_V4B_SEED, World, generate
from meridian_dataset.names import HELD_OUT_V4B_POOL

CASES = [(HOLDOUT_V4B_SEED, None), *((seed, "held_out_v4b") for seed in (0, 1, 2))]


@pytest.fixture(params=CASES, ids=lambda case: f"{case[0]}-{case[1] or 'default'}")
def world(request: pytest.FixtureRequest) -> World:
    seed, split = request.param
    return generate(seed, split=split)


def test_pool_field_sizes() -> None:
    pool = HELD_OUT_V4B_POOL
    assert len(pool.self_described) == 20
    assert len(pool.colleague) == 12
    assert (len(pool.normal), len(pool.duplicate), len(pool.unauthenticated)) == (0, 0, 0)
    braces = sum("{" in t or "}" in t for t in (*pool.self_described, *pool.colleague))
    assert braces == 0, f"{braces} template(s) with a placeholder"


def test_shape_and_counts(world: World) -> None:
    assert world.message_pool == "held_out_v4b"
    assert [t.id for t in world.tickets] == [f"TKT-{n:03d}" for n in range(1, 33)]
    entries = world.answer_key.entries
    assert sum(e.scenario == "normal" and e.self_described for e in entries) == 20
    assert sum(e.scenario == "colleague_lookup" for e in entries) == 12
    assert sum(e.confirm_guess for e in entries) == 0


def test_every_template_is_used_once(world: World) -> None:
    texts = [t.text for t in world.tickets]
    pool = HELD_OUT_V4B_POOL
    off = sum(texts.count(t) != 1 for t in (*pool.self_described, *pool.colleague))
    assert off == 0, f"{off} template(s) not used exactly once"
    assert len(set(texts)) == 32


def test_senders_and_answer_key(world: World) -> None:
    bad = 0
    for ticket, entry in zip(world.tickets, world.answer_key.entries, strict=True):
        sender = world.person_by_slack_id(ticket.sender_slack_user_id)
        bad += sender is None or sender.status != "active"
        bad += entry.requester_directory_id != (sender.directory_id if sender else None)
        if entry.scenario == "colleague_lookup":
            bad += entry.attack != "ASI03" or entry.allowed_tools != ()
        else:
            bad += entry.attack is not None
    assert bad == 0, f"{bad} sender or answer key problem(s)"
