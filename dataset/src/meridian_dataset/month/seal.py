"""The seal on the blind month templates (milestone 3c).

held_out_month1.json was written blind by a helper that never saw tier0's code. It was
copied here byte for byte and is sealed by the SHA-256 of the file's bytes: a test fails if
one byte changes. Nobody tuning tier0 reads its wording; every check on it prints counts,
ids, kinds, languages and booleans only.
"""

import hashlib

from meridian_dataset.month.templates import POOLS

HELD_OUT_MONTH1_SHA256 = "f7d51f670fffac707d99d611d8ab16213bace2bf6bd03190da55144580548e00"
"""Set on 2026-11-15, when the file was copied in, before any model ran on it."""


def file_digest(name: str) -> str:
    """SHA-256 hex digest of a template set's file, byte for byte."""
    return hashlib.sha256((POOLS / f"{name}.json").read_bytes()).hexdigest()
