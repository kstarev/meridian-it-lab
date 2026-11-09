"""The triage prompt is frozen for milestone 2's final comparison (ADR-021)."""

import hashlib

from tier0 import messages
from tier0.evals.prompt_history import CHECK_CHOSEN, CHECK_VERSIONS, CHOSEN, VERSIONS


def test_triage_prompt_matches_its_frozen_hash() -> None:
    digest = hashlib.sha256(messages.TRIAGE_SYSTEM.encode("utf-8")).hexdigest()
    assert digest == messages.TRIAGE_SYSTEM_SHA256


def test_the_chosen_version_is_the_live_prompt() -> None:
    assert VERSIONS[CHOSEN] == messages.TRIAGE_SYSTEM


def test_every_tried_version_is_recorded_and_distinct() -> None:
    assert list(VERSIONS) == ["original", "rewrite", "A", "B", "C", "D", "E", "F"]
    assert len(set(VERSIONS.values())) == len(VERSIONS)


def test_check_prompt_matches_its_frozen_hash() -> None:
    digest = hashlib.sha256(messages.OWN_ACCOUNT_CHECK_SYSTEM.encode("utf-8")).hexdigest()
    assert digest == messages.OWN_ACCOUNT_CHECK_SYSTEM_SHA256


def test_the_chosen_check_version_is_the_live_check_prompt() -> None:
    assert CHECK_VERSIONS[CHECK_CHOSEN] == messages.OWN_ACCOUNT_CHECK_SYSTEM
    assert list(CHECK_VERSIONS) == ["first", "G", "H", "I", "J", "K", "M"]


def test_reports_record_the_frozen_check_prompt() -> None:
    from meridian_dataset import DEFAULT_SEED, generate
    from tier0.evals.report import render_markdown
    from tier0.evals.runner import build_report

    rep = build_report(generate(DEFAULT_SEED), (), seed=1, k=1, provider="ollama", model="m")
    assert rep.check_prompt_sha256 == messages.OWN_ACCOUNT_CHECK_SYSTEM_SHA256
    md = render_markdown(rep)
    assert f"Own-account check prompt: SHA-256 {rep.check_prompt_sha256[:12]}" in md
    assert "(not the frozen prompt)" not in md
