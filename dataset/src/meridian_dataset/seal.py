"""Digest that seals the held-out v2 message pool (ADR-015)."""

import dataclasses
import hashlib
import json

from meridian_dataset.names import MessagePool


def pool_digest(pool: MessagePool) -> str:
    """SHA-256 hex digest of a pool's canonical JSON form."""
    blob = json.dumps(
        dataclasses.asdict(pool), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


HELD_OUT_V2_SHA256 = "be12aa5212429abe828a567aaf258f5fb76cc4e52a2947b65672a7189f716cc1"
"""The seal: if this test fails, someone edited held-out v2, which is not allowed (ADR-015)."""
