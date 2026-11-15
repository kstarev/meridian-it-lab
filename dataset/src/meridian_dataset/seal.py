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

HELD_OUT_V4B_SHA256 = "bf86a6957278f014ae36a0b6288eca6b84414794bdd6e6ce1c63a9df5f6ea2c1"
"""Held-out v4b's seal (ADR-038); set on 2026-11-15, before the TKT-004 fix existed."""

HELD_OUT_V5_SHA256 = "b08988231a5512aa1c84f13c1d3818d6471aa45b445d3526cb685d9976eba8b4"
"""Held-out v5's seal (milestone 3d part 1); set on 2026-11-15, after the plan's decision rule
for Haiku's triage prompt was committed and before any model ran on the set."""

HELD_OUT_V5_LABELS_SHA256 = "f3974f84b06f376699f92503971e9002297ac82129c55f56dfeab9dcea80eca6"
"""The seal on v5's sub-group labels (pools/held_out_v5_labels.json), in canonical JSON."""
