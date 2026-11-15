"""Digests that seal the held-out message pools (ADR-015, ADR-029)."""

import dataclasses
import hashlib
import json

from meridian_dataset.names import MessagePool


def pool_digest(pool: MessagePool) -> str:
    """SHA-256 hex digest of a pool's canonical JSON form."""
    data = dataclasses.asdict(pool)
    # Pools from before the self_described field hash exactly as they did (ADR-029).
    if not data.get("self_described"):
        data.pop("self_described", None)
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(blob).hexdigest()


HELD_OUT_V2_SHA256 = "49d63402909afc1520a6a4a163dee9d5e3a3665723e759a08aaf2e62a918ca72"
"""The seal: if this test fails, someone edited held-out v2, which is not allowed (ADR-015)."""

HELD_OUT_V3_SHA256 = "8ef602f41c696607887e3fad46e55a7b21810620bab45c578bb786fcae89b17a"
"""Held-out v3's seal (ADR-029); set when the blind helper writes the pool."""

HELD_OUT_V4_SHA256 = "1cceb610dbda48a63eafd546b08eb32b11916db5357d8832a4e54b1f4257dc25"
"""Held-out v4's seal (ADR-033); set on 2026-11-15, before the TKT-004 fix existed."""
