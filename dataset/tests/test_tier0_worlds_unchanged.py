"""The access world must not move tier0's worlds: every one keeps its old hash (AAC-004)."""

import hashlib
from pathlib import Path

import pytest

from meridian_dataset import generate, write_dataset

# Manifest hashes of the four tier0 worlds. A change that moves any of them breaks sealed results.
TIER0_MANIFEST_SHA256 = {
    1108: "c540336c28d92651d4e78430ee3620f500aaa34154a2f118e7cb1583c15e2028",  # tuning
    2611: "49e6b0a92a243b282dff8e89a4693d1aa10b1d668442008c885a3ed799670390",  # held_out_v1
    3107: "2bad7bbdee0279f63a17a455b3f95b5f1e885ba11ee6752a934d2165b47485f4",  # held_out_v2
    4417: "5a087f6adb250cc711047f74f683188c5e8ca5f72aec45427b675576bb95cc17",  # held_out_v3
}


@pytest.mark.parametrize("seed", sorted(TIER0_MANIFEST_SHA256))
def test_tier0_world_hash_unchanged(seed: int, tmp_path: Path) -> None:
    paths = write_dataset(generate(seed), tmp_path)
    digest = hashlib.sha256(paths["manifest.json"].read_bytes()).hexdigest()
    assert digest == TIER0_MANIFEST_SHA256[seed]
