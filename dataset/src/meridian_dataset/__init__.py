"""Seeded synthetic dataset for the fictional company Meridian (ADR-001).

Every project in the lab reads the same generated world, so results can be
compared and reproduced from a seed. All data is fake.
"""

from meridian_dataset.export import write_dataset
from meridian_dataset.generator import DEFAULT_SEED, GENERATOR_VERSION, HOLDOUT_SEED, generate
from meridian_dataset.model import World

__all__ = [
    "DEFAULT_SEED",
    "GENERATOR_VERSION",
    "HOLDOUT_SEED",
    "World",
    "generate",
    "write_dataset",
]
