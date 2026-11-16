"""The data model: a grant as the export shows it, and a finding that can only ask for review."""

import dataclasses

import pytest

from meridian_oauth_radar import REVIEW, Finding, Grant, RuleHit

DRIVE = "https://www.googleapis.com/auth/drive"


def grant(**changes: object) -> Grant:
    base: dict[str, object] = {
        "source": "google",
        "user": "sam.okafor@meridian.example",
        "app_id": "000000000001-fakenotesprout.apps.googleusercontent.com",
        "app_name": "NoteSprout AI",
        "scopes": (DRIVE, "openid"),
    }
    return Grant(**(base | changes))  # type: ignore[arg-type]


HIT = RuleHit("broad_scope", 3, "Broad scope: full Drive access.")


def test_a_grant_has_no_field_for_content() -> None:
    names = {f.name for f in dataclasses.fields(Grant)}
    assert names == {"source", "user", "app_id", "app_name", "scopes"}


def test_a_grant_from_an_unknown_source_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        grant(source="slack")


@pytest.mark.parametrize("scopes", [("openid", DRIVE), (DRIVE, DRIVE)])
def test_scopes_must_be_sorted_and_unique(scopes: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        grant(scopes=scopes)


def test_a_finding_can_only_ask_for_review() -> None:
    assert Finding(grant(), (HIT,), "high").action == REVIEW == "review"
    for action in ("revoke", "delete", "suspend", "block", ""):
        with pytest.raises(ValueError, match="never revokes"):
            Finding(grant(), (HIT,), "high", action=action)


def test_a_finding_needs_a_hit_and_a_known_severity() -> None:
    with pytest.raises(ValueError, match="at least one rule hit"):
        Finding(grant(), (), "high")
    with pytest.raises(ValueError, match="unknown severity"):
        Finding(grant(), (HIT,), "critical")


def test_the_score_is_the_sum_of_the_hits() -> None:
    banned = RuleHit("banned_app", 5, "Banned app.")
    assert Finding(grant(), (banned, HIT), "high").score == 8
