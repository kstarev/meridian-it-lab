"""The dry-run policy engine: notice first, then a plan; nothing is ever applied."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from meridian_dataset.oauth import (
    RADAR_DIR,
    REFERENCE_TIME,
    generate_oauth_world,
    write_oauth_world,
)
from meridian_oauth_radar.collector import TokenGrant, collect
from meridian_oauth_radar.enrich import AppFacts, load_knowledge
from meridian_oauth_radar.fake_admin import FakeAdminApi
from meridian_oauth_radar.load import ExportError
from meridian_oauth_radar.policy import (
    Advisory,
    ExceptionEntry,
    PolicyResult,
    Settings,
    decide,
    load_compromised,
    load_exceptions,
    load_ledger,
    policy_files,
)
from meridian_oauth_radar.rubric import Score, score_all, score_grant

G = "https://www.googleapis.com/auth/"
DRIVE = G + "drive"
DAY0 = date(2026, 11, 16)
APP = "000000000901-fakeapp.apps.googleusercontent.com"
OTHER = "000000000902-fakeother.apps.googleusercontent.com"


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


def scored(
    *scope_urls: str,
    unused_days: int | None = 120,
    user: str = "a@meridian.example",
    app: str = APP,
    **fact_changes: object,
) -> Score:
    used = (
        None
        if unused_days is None
        else datetime(2026, 11, 16, 8, tzinfo=UTC) - timedelta(days=unused_days)
    )
    grant = TokenGrant(user, app, "Fake App", tuple(sorted(scope_urls)), False, False, used)
    return score_grant(grant, facts(client_id=app, **fact_changes), 1)


def run(
    found: list[Score], today: date, previous: PolicyResult | None = None, **kw: object
) -> PolicyResult:
    return decide(found, today=today, ledger=previous.ledger if previous else (), **kw)  # type: ignore[arg-type]


def test_a_dormant_restricted_grant_gets_a_notice_first_then_a_plan_after_seven_days() -> None:
    found = [scored(DRIVE)]
    day0 = run(found, DAY0)
    assert [n.issued for n in day0.new_notices] == [DAY0]
    assert day0.plan.status == "empty"
    day3 = run(found, DAY0 + timedelta(days=3), day0)
    assert day3.new_notices == ()
    assert [n.issued for n in day3.waiting] == [DAY0]
    assert day3.plan.status == "empty"
    day7 = run(found, DAY0 + timedelta(days=7), day3)
    assert day7.plan.status == "proposed"
    [item] = day7.plan.items
    assert (item.action, item.notice_issued) == ("revoke", DAY0)
    body = json.loads(policy_files(day7)["plans/after-notice-plan.json"])
    assert (body["dry_run"], body["applied"], body["status"]) == (True, False, "proposed")
    assert body["items"][0]["action"] == "revoke"


def test_a_grant_used_after_its_notice_is_not_planned() -> None:
    day0 = run([scored(DRIVE)], DAY0)
    used_again = [scored(DRIVE, unused_days=-3)]  # used three days after the notice
    day7 = run(used_again, DAY0 + timedelta(days=7), day0)
    assert day7.plan.status == "empty"
    assert day7.ledger == ()
    assert [r.score.grant.key for r in day7.to_a_person] == [used_again[0].grant.key]


def test_same_inputs_same_files_and_no_notice_twice() -> None:
    found = [scored(DRIVE), scored(DRIVE, user="b@meridian.example"), scored("openid")]
    first, second = run(found, DAY0), run(found, DAY0)
    assert policy_files(first) == policy_files(second)
    again = run(found, DAY0, first)
    assert again.new_notices == ()
    assert len(again.waiting) == 2
    later = run(found, DAY0 + timedelta(days=9), first)
    assert later.plan.plan_id == run(found, DAY0 + timedelta(days=9), first).plan.plan_id
    assert later.plan.plan_id != run(found[:1], DAY0 + timedelta(days=9), first).plan.plan_id


def test_exceptions_hold_until_they_expire() -> None:
    entry = ExceptionEntry(
        APP, "a@meridian.example", "quarterly close", "IT lead", date(2026, 11, 30)
    )
    everyone = ExceptionEntry(OTHER, None, "team need", "IT lead", date(2027, 1, 1))
    found = [scored(DRIVE), scored(DRIVE, user="c@meridian.example", app=OTHER)]
    before = run(found, DAY0, exceptions=[entry, everyone])
    assert len(before.excepted) == 2
    assert before.new_notices == ()
    after = run(found, date(2026, 12, 1), exceptions=[entry, everyone])
    assert [n.user_key for n in after.new_notices] == ["a@meridian.example"]
    assert after.expired_exceptions == (entry,)


def test_a_compromised_app_goes_to_an_emergency_plan_a_person_must_trigger() -> None:
    advisory = Advisory(APP, "Fake App", "tokens stolen", date(2026, 11, 14))
    found = [scored(DRIVE, unused_days=1)]
    result = run(found, DAY0, compromised={APP: advisory})
    assert result.new_notices == ()
    assert result.emergency.status == "proposed"
    assert result.emergency.requires_human_trigger is True
    body = json.loads(policy_files(result)["plans/emergency-plan.json"])
    assert (body["applied"], body["requires_human_trigger"]) == (False, True)
    assert "must trigger it" in body["note"]


def test_the_blast_radius_limit_refuses_the_whole_plan() -> None:
    settings = Settings(max_grants_per_plan=10)
    found = [scored(DRIVE, user=f"u{i:02d}@meridian.example") for i in range(11)]
    day0 = run(found, DAY0, settings=settings)
    day7 = run(found, DAY0 + timedelta(days=7), day0, settings=settings)
    assert (day7.plan.status, day7.plan.items, day7.plan.would_touch) == ("refused", (), 11)
    assert len([r for r in day7.to_a_person if "blast-radius" in r.reason]) == 11
    ten = run(found[:10], DAY0 + timedelta(days=7), day0, settings=settings)
    assert (ten.plan.status, len(ten.plan.items)) == ("proposed", 10)


def test_everything_else_goes_to_a_person_or_is_low() -> None:
    no_record = scored(DRIVE, unused_days=None)
    medium = scored(DRIVE, unused_days=2)  # reviewed, verified, full Drive, in use: 3 points
    sign_in = scored("openid", unused_days=200)
    calendar_only = scored(G + "calendar.readonly", unused_days=200, verified_publisher=False)
    result = run([no_record, medium, sign_in, calendar_only], DAY0)
    reasons = {r.score.grant.scopes: r.reason for r in result.to_a_person}
    assert reasons[(DRIVE,)] in {
        "Holds a restricted scope, with no record of use.",
        "Rated medium by the rubric (3 points).",
    }
    assert len(result.to_a_person) == 3
    assert result.new_notices == ()
    assert result.no_action == 1


def test_notice_drafts_say_nothing_was_removed(tmp_path: Path) -> None:
    files = policy_files(run([scored(DRIVE)], DAY0))
    text = files["notices/a@meridian.example.md"].decode()
    assert "Not sent" in text
    assert "Nothing has been removed yet." in text
    assert "before 2026-11-23" in text
    (tmp_path / "ledger.json").write_bytes(files["notices/ledger.json"])
    assert [n.issued for n in load_ledger(tmp_path / "ledger.json")] == [DAY0]
    assert load_ledger(tmp_path / "missing.json") == ()


def test_the_tuning_world_over_a_simulated_week(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    radar = tmp_path / RADAR_DIR
    found = score_all(collect(FakeAdminApi.from_files(radar)).grants, load_knowledge(radar))
    kw = {
        "exceptions": load_exceptions(radar / "exceptions.json"),
        "compromised": load_compromised(radar / "compromised_apps.json"),
    }
    day0 = decide(found, today=REFERENCE_TIME.date(), **kw)  # type: ignore[arg-type]
    week = decide(found, today=REFERENCE_TIME.date() + timedelta(days=7), ledger=day0.ledger, **kw)  # type: ignore[arg-type]
    assert day0.plan.status == "empty"
    assert {(i.user_key, i.client_id) for i in week.plan.items} == {
        (n.user_key, n.client_id) for n in day0.new_notices
    }
    assert len(week.emergency.items) == 1
    assert len(day0.excepted) == 1
    assert len(day0.expired_exceptions) == 1


def test_an_exception_needs_an_expiry_date(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    path.write_text(
        json.dumps(
            {
                "exceptions": [
                    {"client_id": APP, "user_key": None, "reason": "x", "approved_by": "IT lead"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExportError, match='exception 0: "expires" must be a date'):
        load_exceptions(path)
