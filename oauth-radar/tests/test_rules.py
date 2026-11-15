"""The milestone-0 rules: broad_scope and banned_app, and how findings are ranked (OAR-002)."""

import itertools
from pathlib import Path

import pytest

from meridian_oauth_radar import REVIEW, Grant
from meridian_oauth_radar.load import load_banned_apps, load_google_tokens
from meridian_oauth_radar.rules import (
    BANNED_APP_POINTS,
    BROAD_SCOPE_POINTS,
    BROAD_SCOPES,
    RULES,
    banned_app,
    broad_scope,
    scan,
)

APP = "000000000009-fakeapp.apps.googleusercontent.com"


def grant(*scopes: str, app_id: str = APP, user: str = "dana.cruz@meridian.example") -> Grant:
    return Grant("google", user, app_id, "Fake App", tuple(sorted(set(scopes))))


@pytest.mark.parametrize("scope", sorted(BROAD_SCOPES))
def test_each_broad_scope_fires(scope: str) -> None:
    hit = broad_scope(grant(scope, "openid"))
    assert hit is not None
    assert hit.rule == "broad_scope"
    assert hit.points == BROAD_SCOPE_POINTS
    assert scope in hit.reason
    assert BROAD_SCOPES[scope] in hit.reason


@pytest.mark.parametrize(
    "scope",
    [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
    ],
)
def test_narrower_scopes_do_not_fire(scope: str) -> None:
    assert broad_scope(grant(scope)) is None


def test_one_hit_names_every_broad_scope_the_grant_holds() -> None:
    mail = "https://www.googleapis.com/auth/gmail.readonly"
    drive = "https://www.googleapis.com/auth/drive.readonly"
    hit = broad_scope(grant(mail, drive, "openid"))
    assert hit is not None
    assert mail in hit.reason
    assert drive in hit.reason
    assert "openid" not in hit.reason


def test_banned_app_fires_only_for_a_listed_app() -> None:
    banned = {APP: "banned for the test"}
    hit = banned_app(grant("openid"), banned)
    assert hit is not None
    assert (hit.rule, hit.points) == ("banned_app", BANNED_APP_POINTS)
    assert "banned for the test" in hit.reason
    other = "000000000010-fakeother.apps.googleusercontent.com"
    assert banned_app(grant("openid", app_id=other), banned) is None
    assert banned_app(grant("openid"), {}) is None


def test_the_fixture_gives_four_ranked_findings(tokens_path: Path, banned_path: Path) -> None:
    findings = scan(load_google_tokens(tokens_path), load_banned_apps(banned_path))
    ranked = [
        (f.grant.app_name, f.grant.user.split("@")[0], [h.rule for h in f.hits], f.score)
        for f in findings
    ]
    assert ranked == [
        ("NoteSprout AI", "sam.okafor", ["banned_app", "broad_scope"], 8),
        ("NoteSprout AI", "priya.nair", ["banned_app"], 5),
        ("Taskmole Agent", "jordan.weber", ["broad_scope"], 3),
        ("Wordloom Assistant", "alex.kim", ["broad_scope"], 3),
    ]
    assert {f.severity for f in findings} == {"high"}


def test_without_the_banned_list_only_broad_scopes_are_found(tokens_path: Path) -> None:
    findings = scan(load_google_tokens(tokens_path))
    assert [f.grant.user.split("@")[0] for f in findings] == [
        "sam.okafor",
        "jordan.weber",
        "alex.kim",
    ]


def test_every_finding_asks_only_for_review(tokens_path: Path, banned_path: Path) -> None:
    findings = scan(load_google_tokens(tokens_path), load_banned_apps(banned_path))
    assert findings
    assert {f.action for f in findings} == {REVIEW}


def test_the_same_grants_in_any_order_give_the_same_list(
    tokens_path: Path, banned_path: Path
) -> None:
    grants = load_google_tokens(tokens_path)
    banned = load_banned_apps(banned_path)
    expected = scan(grants, banned)
    for order in itertools.permutations(grants):
        assert scan(order, banned) == expected


def test_no_grants_no_findings() -> None:
    assert scan([]) == ()
    assert scan([grant("openid")]) == ()


def test_the_rule_list_matches_the_rules_that_run() -> None:
    assert set(RULES) == {"broad_scope", "banned_app"}
    hits = scan([grant(*BROAD_SCOPES)], {APP: "test"})[0].hits
    assert {hit.rule for hit in hits} == set(RULES)
