"""The triage prompt is frozen for milestone 2's final comparison (ADR-021)."""

import hashlib

from tier0 import messages
from tier0.evals.prompt_history import CHOSEN, VERSIONS


def test_triage_prompt_matches_its_frozen_hash() -> None:
    digest = hashlib.sha256(messages.TRIAGE_SYSTEM.encode("utf-8")).hexdigest()
    assert digest == messages.TRIAGE_SYSTEM_SHA256


def test_the_chosen_version_is_the_live_prompt() -> None:
    assert VERSIONS[CHOSEN] == messages.TRIAGE_SYSTEM


def test_every_tried_version_is_recorded_and_distinct() -> None:
    assert list(VERSIONS) == ["original", "rewrite", "A", "B", "C", "D", "E", "F"]
    assert len(set(VERSIONS.values())) == len(VERSIONS)
