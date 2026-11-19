"""The rubric of OAR-008: OAR-002's points, with the owner's two changes (2026-11-19)."""

import itertools
import json
from datetime import date
from pathlib import Path

import pytest

from meridian_dataset.oauth import RADAR_DIR, generate_oauth_world, write_oauth_world
from meridian_oauth_radar.collector import TokenGrant
from meridian_oauth_radar.enrich import AppFacts, load_knowledge, load_leavers
from meridian_oauth_radar.load import ExportError
from meridian_oauth_radar.rubric import (
    APPROVED_REACH_NOTE,
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
        "vendor_ai": False,
        "banned_reason": None,
        "approved_alternative": None,
    }
    return AppFacts(**(base | changes))  # type: ignore[arg-type]


def points(score: Score) -> dict[str, int]:
    return {p.signal: p.points for p in score.signals}


def test_it_names_its_decision() -> None:
    assert STATUS == "OAR-008, accepted by the owner on 2026-11-19"
    assert score_grant(grant("openid"), facts(), 1).status == STATUS


def test_oar_008_worked_examples() -> None:
    # An unverified app with full Drive access: 3 + 2 = 5, high.
    s = score_grant(grant(DRIVE), facts(verified_publisher=False), 1)
    assert (s.total, s.tier, s.tier_note) == (5, HIGH, None)
    # A verified AI note-taker IT approved, 25 people, reading calendars: 2 + 2 + 1 = 5, but
    # 3 without reach, so medium (OAR-002 rated it high).
    s = score_grant(grant(CAL, name="Notes"), facts(vendor_ai=True), 25)
    assert (s.total, s.tier) == (5, MEDIUM)
    assert s.tier_note == APPROVED_REACH_NOTE.format(total=5)
    assert points(s) == {
        "scope_breadth": 2,
        "unverified_publisher": 0,
        "reach": 2,
        "ai_app": 1,
        "not_reviewed": 0,
    }
    # The same app, reviewed by IT but not approved, stays high.
    s = score_grant(grant(CAL, name="Notes"), facts(vendor_ai=True, it_status="not approved"), 25)
    assert (s.total, s.tier, s.tier_note) == (5, HIGH, None)
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


def ai_points(name: str, app_facts: AppFacts) -> int:
    return points(score_grant(grant("openid", name=name), app_facts, 1))["ai_app"]


def test_the_vendor_directorys_ai_flag_comes_first() -> None:
    # The directory knows the app: its flag decides, whatever the name or IT's tag says.
    assert ai_points("Scribble Recorder", facts(vendor_ai=True)) == 1
    assert ai_points("Quick AI Notes", facts(vendor_ai=False)) == 0
    assert ai_points("Plain", facts(vendor_ai=False, it_ai_tag=True)) == 0
    assert ai_points("Plain", facts(vendor_ai=True, it_ai_tag=False)) == 1
    s = score_grant(grant("openid", name="Plain"), facts(vendor_ai=True), 1)
    assert "the vendor directory flags it as AI" in s.signals[3].reason


def test_the_word_list_only_when_the_directory_does_not_know_the_app() -> None:
    unknown = facts(vendor=None, verified_publisher=None, vendor_ai=None)
    assert ai_points("Quick AI Notes", unknown) == 1
    assert ai_points("Recorder", unknown) == 0
    # IT's own tag no longer counts (OAR-008): only the name.
    tagged = facts(vendor=None, verified_publisher=None, vendor_ai=None, it_ai_tag=True)
    assert ai_points("Plain", tagged) == 0
    s = score_grant(grant("openid", name="Notes GPT"), unknown, 1)
    assert "its name matches the AI word list (gpt)" in s.signals[3].reason


def test_an_approved_app_is_never_high_on_reach_alone() -> None:
    # Approved, verified, full Drive, AI: 3 + 0 + 2 + 1 + 0 = 6; 4 without reach: medium.
    s = score_grant(grant(DRIVE), facts(vendor_ai=True), 111)
    assert (s.total, s.tier) == (6, MEDIUM)
    assert s.tier_note == APPROVED_REACH_NOTE.format(total=6)
    # The 5-24 step counts as reach too: 3 + 1 + 1 = 5, 4 without it.
    assert score_grant(grant(DRIVE), facts(vendor_ai=True), 5).tier == MEDIUM
    # High through its other signals: approved but unverified, full Drive: 3 + 2 = 5.
    s = score_grant(grant(DRIVE), facts(verified_publisher=False), 111)
    assert (s.total, s.tier, s.tier_note) == (7, HIGH, None)
    # Not approved by IT: reach still counts.
    for status in (None, "not approved"):
        s = score_grant(grant(DRIVE), facts(vendor_ai=True, it_status=status), 111)
        assert (s.tier, s.tier_note) == (HIGH, None), status
    # An override still makes an approved app high.
    s = score_grant(grant(DRIVE), facts(vendor_ai=True, banned_reason="copies files"), 111)
    assert (s.tier, s.tier_note) == (HIGH, None)


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
    assert (assistant.it_status, assistant.vendor_ai, assistant.approved_alternative) == (
        "approved",
        True,
        None,
    )
    recorder = knowledge.facts(by_name["Scribblebee Recorder"])
    assert (recorder.in_it_catalogue, recorder.vendor_ai) == (False, True)
    unknown = knowledge.facts("000000000999-fakenobody.apps.googleusercontent.com")
    assert (
        unknown.vendor,
        unknown.verified_publisher,
        unknown.in_it_catalogue,
        unknown.vendor_ai,
    ) == (None, None, False, None)
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


def test_leavers_are_read_from_hr_and_their_grants_are_high(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    folder = tmp_path / RADAR_DIR
    today = date(2026, 11, 16)
    left = load_leavers(folder, today)
    world = generate_oauth_world()
    assert left == {leaver.work_email for leaver in world.leavers}
    # "Terminated", or an end date that has passed; a future end date isn't gone yet.
    path = folder / "hris_leavers.json"
    rows = [
        {"employee_id": "E1", "work_email": "a@x", "status": "terminated", "end_date": ""},
        {"employee_id": "E2", "work_email": "b@x", "status": "active", "end_date": "2026-11-15"},
        {"employee_id": "E3", "work_email": "c@x", "status": "active", "end_date": "2026-11-16"},
        {"employee_id": "E4", "work_email": "d@x", "status": "active", "end_date": ""},
    ]
    path.write_text(json.dumps({"records": rows}), encoding="utf-8")
    assert load_leavers(folder, today) == {"a@x", "b@x"}
    rows[0]["end_date"] = "soon"
    path.write_text(json.dumps({"records": rows}), encoding="utf-8")
    with pytest.raises(ExportError, match='record 0: "end_date" must be a date'):
        load_leavers(folder, today)
