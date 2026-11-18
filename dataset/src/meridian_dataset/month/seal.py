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

HELD_OUT_MONTH2_SHA256 = "39175f9cf35532fece58182f93e52ccbf0c4683f9e453d6bce437b5b8059ee0e"
"""Set on 2026-11-18, when the file was copied in byte for byte from the blind writer's output,
before any model ran on it. Nobody tuning tier0 reads its wording; every check on it prints
counts, ids, kinds, languages and booleans only."""

HELD_OUT_MONTH2B_SHA256 = "f3fd0b58f43f4174c5fa539d7d3ede25d67d157983dba8434c9c421fbe666949"
"""Set on 2026-11-18, the same way as HELD_OUT_MONTH2_SHA256, before any model ran on it. Sealed
again the same day, before any model ran on it: its writer added the slowness_only tag to each
template and changed nothing else (first seal f4fbe811…8402a86; the owner's ruling and
docs/plans/milestone-3e-month2b-rules.md)."""


def file_digest(name: str) -> str:
    """SHA-256 hex digest of a template set's file, byte for byte."""
    return hashlib.sha256((POOLS / f"{name}.json").read_bytes()).hexdigest()
