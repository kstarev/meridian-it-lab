"""The seals on the knowledge sets (ADR-035, like ADR-029).

A blind helper wrote `pools/held_out_k1.json` on 2026-11-15, before any picker prompt had been
scored. The digest below was recorded then. If the test that compares them fails, someone
edited the sealed set, which is not allowed. `held_out_g2`, the guide screen's set, is sealed
the same way when it is written.
"""

import hashlib
import json

from meridian_dataset.knowledge import POOLS


def question_set_digest(name: str) -> str:
    """SHA-256 hex digest of a question pool's canonical JSON form."""
    raw = json.loads((POOLS / f"{name}.json").read_text(encoding="utf-8"))
    blob = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


HELD_OUT_K1_SHA256 = "c62b099a8a159c1a46ad1ed8e8236625de97d5f02a0677e8716f4fcfd4fe4e62"
"""Held-out how-to test k1's seal; set on 2026-11-15, before the first paid picker call."""

HELD_OUT_G2_SHA256: str | None = None
"""The guide screen's sealed guide set g2 (docs/plans/milestone-4-guide-screen-fix.md). None
until the set is written: the seal commit adds `pools/held_out_g2.json` and sets this digest
together, and a test fails if only one of the two is there."""
