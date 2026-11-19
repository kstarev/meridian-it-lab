"""Slack nudge drafts: one file per user, for risky apps still in use. Nothing is sent."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from meridian_dataset.oauth import (
    RADAR_DIR,
    REFERENCE_TIME,
    generate_oauth_world,
    write_oauth_world,
)
from meridian_oauth_radar.collector import TokenGrant, collect
from meridian_oauth_radar.enrich import CatalogueEntry, Knowledge, VendorEntry, load_knowledge
from meridian_oauth_radar.fake_admin import FakeAdminApi
from meridian_oauth_radar.nudges import nudge_files, nudges
from meridian_oauth_radar.policy import decide, load_compromised, load_exceptions
from meridian_oauth_radar.rubric import score_all

G = "https://www.googleapis.com/auth/"
DAY0 = date(2026, 11, 16)
RISKY = "000000000911-fakerisky.apps.googleusercontent.com"
OK = "000000000912-fakeapproved.apps.googleusercontent.com"


def knowledge() -> Knowledge:
    vendor = VendorEntry(
        RISKY, "Risky Notes AI", "Risky", "ai assistant", True, False, "unknown", "young"
    )
    approved = CatalogueEntry(OK, "Approved Assist", "ai assistant", True, "approved")
    return Knowledge({RISKY: vendor}, {OK: approved}, {})


def grant(
    client: str, name: str, *scope_urls: str, unused: int, user: str = "a@meridian.example"
) -> TokenGrant:
    used = datetime(2026, 11, 16, 8, tzinfo=UTC) - timedelta(days=unused)
    return TokenGrant(user, client, name, tuple(sorted(scope_urls)), False, False, used)


def test_a_risky_app_in_use_gets_the_catalogue_nudge() -> None:
    grants = [
        grant(RISKY, "Risky Notes AI", G + "drive.readonly", "openid", unused=3),
        grant(RISKY, "Risky Notes AI", G + "drive.readonly", unused=200, user="b@meridian.example"),
        grant(OK, "Approved Assist", G + "drive.readonly", unused=1, user="c@meridian.example"),
    ]
    k = knowledge()
    result = decide(score_all(grants, k), today=DAY0)
    [nudge] = nudges(result, k)
    assert nudge.user_key == "a@meridian.example"
    assert nudge.text == (
        "*Risky Notes AI* is connected to your Google account. This app can see and download "
        "all your Drive files. Here's the approved tool that does the same thing: "
        "*Approved Assist*. If you switch, you can disconnect Risky Notes AI."
    )
    files = nudge_files(nudges(result, k), DAY0.isoformat())
    assert set(files) == {"nudges/a@meridian.example.md", "nudges/index.json"}
    assert "Not sent" in files["nudges/a@meridian.example.md"].decode()
    assert json.loads(files["nudges/index.json"])["sent"] == 0


def test_no_alternative_says_so() -> None:
    k = Knowledge(knowledge().vendors, {}, {})
    result = decide(
        score_all([grant(RISKY, "Risky Notes AI", G + "drive", unused=1)], k), today=DAY0
    )
    [nudge] = nudges(result, k)
    assert nudge.alternative is None
    assert "IT hasn't approved a tool for this yet" in nudge.text


def test_the_tuning_world_nudges(tmp_path: Path) -> None:
    write_oauth_world(generate_oauth_world(), tmp_path)
    radar = tmp_path / RADAR_DIR
    k = load_knowledge(radar)
    result = decide(
        score_all(collect(FakeAdminApi.from_files(radar)).grants, k),
        today=REFERENCE_TIME.date(),
        exceptions=load_exceptions(radar / "exceptions.json").entries,
        compromised=load_compromised(radar / "compromised_apps.json"),
    )
    found = nudges(result, k)
    apps = {n.app for n in found}
    assert "Vellumine Assist" not in apps  # IT approved it
    assert "Synapsel Agent" not in apps  # compromised: emergency plan instead
    assert "Murmurlark Notes AI" not in apps  # unused: a notice instead
    assert {"Draftmoth AI Writer", "Scribblebee Recorder"} <= apps
    assert nudge_files(found, "x") == nudge_files(nudges(result, k), "x")
