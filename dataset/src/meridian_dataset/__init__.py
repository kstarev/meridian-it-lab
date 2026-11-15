"""Seeded synthetic dataset for the fictional company Meridian (ADR-001).

Every project in the lab reads the same generated world, so results can be
compared and reproduced from a seed. All data is fake.
"""

from meridian_dataset.export import write_dataset
from meridian_dataset.generator import (
    DEFAULT_SEED,
    GENERATOR_VERSION,
    HOLDOUT_SEED,
    HOLDOUT_V2_SEED,
    HOLDOUT_V3_SEED,
    HOLDOUT_V4_SEED,
    HOLDOUT_V4B_SEED,
    generate,
)
from meridian_dataset.model import World

__all__ = [
    "DEFAULT_SEED",
    "GENERATOR_VERSION",
    "HOLDOUT_SEED",
    "HOLDOUT_V2_SEED",
    "HOLDOUT_V3_SEED",
    "HOLDOUT_V4B_SEED",
    "HOLDOUT_V4_SEED",
    "World",
    "generate",
    "write_dataset",
]
