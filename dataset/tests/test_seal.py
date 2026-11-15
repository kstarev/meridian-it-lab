"""The held-out v2 pool is sealed (ADR-015). Failures print digests, never wording."""

import dataclasses

from meridian_dataset.names import (
    HELD_OUT_V2_POOL,
    HELD_OUT_V3_POOL,
    HELD_OUT_V4_POOL,
    TUNING_POOL,
)
from meridian_dataset.seal import (
    HELD_OUT_V2_SHA256,
    HELD_OUT_V3_SHA256,
    HELD_OUT_V4_SHA256,
    pool_digest,
)


def test_held_out_v2_is_unchanged() -> None:
    assert pool_digest(HELD_OUT_V2_POOL) == HELD_OUT_V2_SHA256


def test_held_out_v3_is_unchanged() -> None:
    assert pool_digest(HELD_OUT_V3_POOL) == HELD_OUT_V3_SHA256


def test_held_out_v4_is_unchanged() -> None:
    assert pool_digest(HELD_OUT_V4_POOL) == HELD_OUT_V4_SHA256


def test_digest_is_deterministic() -> None:
    assert pool_digest(TUNING_POOL) == pool_digest(TUNING_POOL)


def test_digest_changes_with_one_character() -> None:
    first = TUNING_POOL.normal[0]
    edited = dataclasses.replace(TUNING_POOL, normal=("X" + first[1:], *TUNING_POOL.normal[1:]))
    assert pool_digest(edited) != pool_digest(TUNING_POOL)
