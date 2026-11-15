"""The held-out v5 world (milestone 3d part 1): injection and relayed or forwarded requests.

Assertions are counts and booleans only, so a failure never prints v5 wording.
"""

import collections
import json
from pathlib import Path

from meridian_dataset import HOLDOUT_V5_SEED, generate
from meridian_dataset.names import HELD_OUT_V5_POOL

LABELS = Path(__file__).resolve().parents[1] / "src/meridian_dataset/pools/held_out_v5_labels.json"


def test_v5_uses_every_template_once() -> None:
    world = generate(HOLDOUT_V5_SEED)
    assert world.message_pool == "held_out_v5"
    assert len(world.tickets) == 66
    scenarios = collections.Counter(
        (e.scenario, e.self_described) for e in world.answer_key.entries
    )
    assert scenarios == {
        ("normal", False): 20,
        ("normal", True): 12,
        ("colleague_lookup", False): 28,
        ("unauthenticated", False): 6,
    }
    assert not any("{" in t.text or "}" in t.text for t in world.tickets)


def test_v5_labels_match_its_groups() -> None:
    labels = json.loads(LABELS.read_text("utf-8"))
    pool = HELD_OUT_V5_POOL
    for group in ("normal", "self_described", "colleague", "unauthenticated"):
        assert len(labels[group]) == len(getattr(pool, group))
    counts = {g: collections.Counter(v) for g, v in labels.items()}
    assert counts["normal"] == {"plain": 12, "injected": 8}
    assert counts["colleague"] == {"relayed": 10, "forwarded": 6, "injected": 12}
    assert counts["unauthenticated"] == {"plain": 3, "injected": 3}
    named = collections.Counter(
        label
        for t, label in zip(pool.colleague, labels["colleague"], strict=True)
        if "{target}" in t
    )
    assert named == {"relayed": 5, "forwarded": 3, "injected": 6}
