"""The SHA-256 of the month templates written blind (milestone 3c).

held_out_month1.json was written blind by a helper that never saw tier0's code. It was
copied here byte for byte and sealed by the SHA-256 of the file's bytes until its one final
test on 2026-11-17. It is a dev set since (SPENT_SETS) and may be read. The digest still
pins it, because the published result of 2026-11-17 was measured on exactly these bytes: a
test fails if one byte changes.
"""

import hashlib

from meridian_dataset.month.templates import POOLS

HELD_OUT_MONTH1_SHA256 = "f7d51f670fffac707d99d611d8ab16213bace2bf6bd03190da55144580548e00"
"""Set on 2026-11-15, when the file was copied in, before any model ran on it."""


def file_digest(name: str) -> str:
    """SHA-256 hex digest of a template set's file, byte for byte."""
    return hashlib.sha256((POOLS / f"{name}.json").read_bytes()).hexdigest()
