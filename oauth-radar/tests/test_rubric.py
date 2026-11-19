"""The OAR-002 rubric, as OAR-002 states it (Proposed, not yet accepted by the owner)."""

import itertools
import json
from pathlib import Path

import pytest

from meridian_dataset.oauth import RADAR_DIR, generate_oauth_world, write_oauth_world
from meridian_oauth_radar.collector import TokenGrant
from meridian_oauth_radar.enrich import AppFacts, load_knowledge
from meridian_oauth_radar.load import ExportError
from meridian_oauth_radar.rubric import (
    HIGH,
    LOW,
    MEDIUM,
    SIGNALS,
    STATUS,
    Score,
    score_all,
    score_grant,
)

G = "https://www.googleapis.com/auth/"
DRIVE = G + "drive"
CAL = G + "calendar.readonly"
APP = "000000000901-fakeapp.apps.googleusercontent.com"


def grant(*scope_urls: str, name: str = "Fake App", user: str = "a@meridian.example") -> TokenGrant:
    return TokenGrant(user, APP, name, tuple(sorted(set(scope_urls))), False, False, None)


def facts(**changes: object) -> AppFacts:
    base: dict[str, object] = {
        "client_id": APP,
        "vendor": "Fake Vendor",
        "category": "tools",
        "verified_publisher": True,
        "data_location": "EU",
        "vendor_maturity": "established",
        "in_it_catalogue": True,
        "it_status": "approved",
        "it_ai_tag": False,
        "banned_reason": None,
        "approved_alternative": None,
    }
    return AppFacts(**(base | changes))  # type: ignore[arg-type]


def points(score: Score) -> dict[str, int]:
    return {p.signal: p.points for p in score.signals}


def test_it_is_marked_proposed() -> None:
    assert STATUS == "Proposed (OAR-002), not yet accepted by the owner"
    assert score_grant(grant("openid"), facts(), 1).status == STATUS


def test_oar_002_worked_examples() -> None:
    # An unverified app with full Drive access: 3 + 2 = 5, high.
    s = score_grant(grant(DRIVE), facts(verified_publisher=False), 1)
    assert (s.total, s.tier) == (5, HIGH)
    # A verified AI note-taker in the catalogue, org-wide, reading calendars: 2 + 2 + 1 = 5.
    s = score_grant(grant(CAL, name="Notes"), facts(it_ai_tag=True), 25)
    assert (s.total, s.tier) == (5, HIGH)
    assert points(s) == {
        "scope_breadth": 2,
        "unverified_publisher": 0,
        "reach": 2,
        "ai_app": 1,
        "not_reviewed": 0,
    }
    # A verified, reviewed app with full Drive for one person: 3, medium.
    s = score_grant(grant(DRIVE), facts(), 1)
    assert (s.total, s.tier) == (3, MEDIUM)
    # "Sign in with Google" for one person: 0, low.
    s = score_grant(grant("openid", G + "userinfo.email"), facts(), 1)
    assert (s.total, s.tier) == (0, LOW)


def test_every_score_shows_its_points_per_signal() -> None:
    s = score_grant(
        grant(DRIVE, "openid"), facts(in_it_catalogue=False, verified_publisher=None), 7
    )
    assert [p.signal for p in s.signals] == list(SIGNALS)
    assert all(p.reason for p in s.signals)
    assert s.total == sum(p.points for p in s.signals) == 3 + 2 + 1 + 0 + 1
    assert "see, change, share and delete all your Drive files" in s.signals[0].reason


def test_an_unknown_scope_counts_as_broad() -> None:
    s = score_grant(grant(G + "made.up"), facts(), 1)
    assert points(s)["scope_breadth"] == 3
    assert "isn't in the scope table" in s.signals[0].reason


def test_the_broadest_scope_counts_not_the_sum() -> None:
    many_small = grant(CAL, G + "contacts.readonly", G + "spreadsheets", G + "tasks")
    assert points(score_grant(many_small, facts(), 1))["scope_breadth"] == 2
    assert points(score_grant(grant("https://mail.google.com/"), facts(), 1))["scope_breadth"] == 3


@pytest.mark.parametrize(("users", "extra"), [(1, 0), (4, 0), (5, 1), (24, 1), (25, 2), (200, 2)])
def test_reach(users: int, extra: int) -> None:
    assert points(score_grant(grant("openid"), facts(), users))["reach"] == extra


def test_the_ai_word_list_only_applies_outside_the_catalogue() -> None:
    outside = facts(in_it_catalogue=False, it_status=None, it_ai_tag=None)
    assert points(score_grant(grant("openid", name="Quick AI Notes"), outside, 1))["ai_app"] == 1
    assert points(score_grant(grant("openid", name="Recorder"), outside, 1))["ai_app"] == 0
    # IT's tag wins over the name.
    assert points(score_grant(grant("openid", name="Quick AI Notes"), facts(), 1))["ai_app"] == 0
    assert (
        points(score_grant(grant("openid", name="Plain"), facts(it_ai_tag=True), 1))["ai_app"] == 1
    )


def test_not_reviewed_and_an_unknown_publisher() -> None:
    s = score_grant(grant("openid"), facts(in_it_catalogue=False, verified_publisher=None), 1)
    assert points(s)["not_reviewed"] == 1
    assert points(s)["unverified_publisher"] == 2


def test_overrides_make_a_grant_high_whatever_its_points() -> None:
    banned = score_grant(grant("openid"), facts(banned_reason="copies files"), 1)
    assert (banned.total, banned.tier) == (0, HIGH)
    assert banned.overrides[0].signal == "banned_app"
    leaver = score_grant(grant(CAL), facts(), 1, leaver=True)
    assert leaver.tier == HIGH
    assert score_grant(grant("openid"), facts(), 1, leaver=True).tier == LOW


def test_the_same_grants_in_any_order_give_the_same_ranking(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    knowledge = load_knowledge(tmp_path / RADAR_DIR)
    grants = [
        TokenGrant(f"u{i}@meridian.example", APP, "Fake App", (DRIVE,), False, False, None)
        for i in range(6)
    ] + [grant("openid", name="Other", user="z@meridian.example")]
    first = score_all(grants, knowledge)
    for order in itertools.islice(itertools.permutations(grants), 20):
        assert score_all(order, knowledge) == first
    # 6 people hold APP, so each of them gets the reach point.
    assert {points(s)["reach"] for s in first if s.grant.client_id == APP} == {1}


def test_the_enricher_reads_the_tuning_world(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    knowledge = load_knowledge(tmp_path / RADAR_DIR)
    by_name = {v.name: v.client_id for v in knowledge.vendors.values()}
    draft = knowledge.facts(by_name["Draftmoth AI Writer"])
    assert draft.in_it_catalogue is False
    assert draft.verified_publisher is False
    assert draft.approved_alternative == "Vellumine Assist"
    assistant = knowledge.facts(by_name["Vellumine Assist"])
    assert (assistant.it_status, assistant.it_ai_tag, assistant.approved_alternative) == (
        "approved",
        True,
        None,
    )
    unknown = knowledge.facts("000000000999-fakenobody.apps.googleusercontent.com")
    assert (unknown.vendor, unknown.verified_publisher, unknown.in_it_catalogue) == (
        None,
        None,
        False,
    )
    assert len(knowledge.banned) == 1


def test_a_bad_catalogue_stops_the_load(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    folder = tmp_path / RADAR_DIR
    path = folder / "it_app_catalogue.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["apps"][0]["status"] = "maybe"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExportError, match='app 0: "status"'):
        load_knowledge(folder)
