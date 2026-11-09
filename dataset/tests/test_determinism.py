"""Same seed, same bytes."""

import hashlib
from pathlib import Path

from meridian_dataset import DEFAULT_SEED, generate, write_dataset

# When the generator changes on purpose, bump GENERATOR_VERSION and update this
# hash in the same commit.
GOLDEN_MANIFEST_SHA256 = "02548d6e1b3a1ec0aa7606b768b01ff37e5e40d8ac5b75413bc9ee46c78317fd"


def test_same_seed_gives_equal_worlds() -> None:
    assert generate(5) == generate(5)


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    first = write_dataset(generate(DEFAULT_SEED), tmp_path / "a")
    second = write_dataset(generate(DEFAULT_SEED), tmp_path / "b")
    assert first.keys() == second.keys()
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes()


def test_different_seeds_give_different_worlds(tmp_path: Path) -> None:
    one = write_dataset(generate(1), tmp_path / "one")["world.json"]
    two = write_dataset(generate(2), tmp_path / "two")["world.json"]
    assert one.read_bytes() != two.read_bytes()


def test_golden_manifest_hash(tmp_path: Path) -> None:
    paths = write_dataset(generate(DEFAULT_SEED), tmp_path)
    digest = hashlib.sha256(paths["manifest.json"].read_bytes()).hexdigest()
    assert digest == GOLDEN_MANIFEST_SHA256
