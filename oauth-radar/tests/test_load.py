"""The loader reads a Google token export and IT's banned list, and never drops a row silently."""

import json
from pathlib import Path
from typing import Any

import pytest

from meridian_oauth_radar.load import (
    GOOGLE_KIND,
    ExportError,
    load_banned_apps,
    load_google_tokens,
)


def write(tmp_path: Path, data: Any, name: str = "export.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def token(**changes: Any) -> dict[str, Any]:
    base = {
        "kind": "admin#directory#token",
        "clientId": "000000000009-fakeapp.apps.googleusercontent.com",
        "displayText": "Fake App",
        "userKey": "dana.cruz@meridian.example",
        "scopes": ["openid"],
    }
    return base | changes


def test_the_fixture_loads(tokens_path: Path) -> None:
    grants = load_google_tokens(tokens_path)
    assert len(grants) == 6
    first = grants[0]
    assert first.source == "google"
    assert first.user == "sam.okafor@meridian.example"
    assert first.app_id == "000000000001-fakenotesprout.apps.googleusercontent.com"
    assert first.app_name == "NoteSprout AI"
    assert first.scopes == (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/drive",
        "openid",
    )


def test_the_fixtures_are_fake(tokens_path: Path, banned_path: Path) -> None:
    data = json.loads(tokens_path.read_text(encoding="utf-8"))
    for item in data["items"]:
        assert item["userKey"].endswith("@meridian.example")
        assert "-fake" in item["clientId"]
    for app in json.loads(banned_path.read_text(encoding="utf-8"))["apps"]:
        assert "-fake" in app["app_id"]


def test_scopes_are_sorted_and_deduplicated(tmp_path: Path) -> None:
    scopes = ["openid", "https://mail.google.com/", "openid"]
    path = write(tmp_path, {"kind": GOOGLE_KIND, "items": [token(scopes=scopes)]})
    assert load_google_tokens(path)[0].scopes == ("https://mail.google.com/", "openid")


def test_extra_fields_never_reach_a_grant(tmp_path: Path) -> None:
    item = token(etag='"x"', nativeApp=True, content="Subject: payroll; body: secret words")
    path = write(tmp_path, {"kind": GOOGLE_KIND, "items": [item]})
    (loaded,) = load_google_tokens(path)
    assert "secret" not in repr(loaded)
    assert "payroll" not in repr(loaded)


def test_items_left_out_means_no_grants(tmp_path: Path) -> None:
    assert load_google_tokens(write(tmp_path, {"kind": GOOGLE_KIND})) == ()


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("not an object", "not an object"),
        (token(userKey=None), '"userKey" must be a non-empty string'),
        (token(clientId=""), '"clientId" must be a non-empty string'),
        (token(displayText="  "), '"displayText" must be a non-empty string'),
        (token(scopes="openid"), '"scopes" must be a list'),
        (token(scopes=["openid", 7]), '"scopes" must be a list'),
        (token(scopes=None), '"scopes" must be a list'),
    ],
)
def test_a_bad_item_stops_the_load_and_says_where(tmp_path: Path, bad: Any, message: str) -> None:
    path = write(tmp_path, {"kind": GOOGLE_KIND, "items": [token(), bad]})
    with pytest.raises(ExportError, match="item 1") as err:
        load_google_tokens(path)
    assert message in str(err.value)


@pytest.mark.parametrize(
    "data",
    [[], {"kind": "admin#directory#userList", "items": []}, {"items": []}],
)
def test_a_file_of_another_shape_is_refused(tmp_path: Path, data: Any) -> None:
    with pytest.raises(ExportError, match="not a Google token export"):
        load_google_tokens(write(tmp_path, data))


def test_items_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match='"items" must be a list'):
        load_google_tokens(write(tmp_path, {"kind": GOOGLE_KIND, "items": {}}))


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExportError, match="can't read it as JSON"):
        load_google_tokens(path)
    with pytest.raises(ExportError, match="can't read it as JSON"):
        load_google_tokens(tmp_path / "missing.json")


def test_the_banned_list_loads(banned_path: Path) -> None:
    banned = load_banned_apps(banned_path)
    assert list(banned) == ["000000000001-fakenotesprout.apps.googleusercontent.com"]
    assert "AI note-taker" in banned["000000000001-fakenotesprout.apps.googleusercontent.com"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"banned": []}, 'needs an "apps" list'),
        ([], 'needs an "apps" list'),
        ({"apps": ["x"]}, "app 0: not an object"),
        ({"apps": [{"app_id": "a"}]}, '"reason" must be a non-empty string'),
        ({"apps": [{"reason": "r"}]}, '"app_id" must be a non-empty string'),
        (
            {"apps": [{"app_id": "a", "reason": "r"}, {"app_id": "a", "reason": "r"}]},
            "listed twice",
        ),
    ],
)
def test_a_bad_banned_list_is_refused(tmp_path: Path, data: Any, message: str) -> None:
    with pytest.raises(ExportError) as err:
        load_banned_apps(write(tmp_path, data))
    assert message in str(err.value)
