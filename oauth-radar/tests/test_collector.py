"""The mock Google Admin token API and the collector that pages through it (milestone 1).

No real HTTP client is used anywhere: one test makes every socket call fail and collects
anyway, and the guards in test_guards.py check the source imports no network library.
"""

import dataclasses
import inspect
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian_oauth_radar.collector import Collection, CollectorError, TokenGrant, collect
from meridian_oauth_radar.fake_admin import (
    ACTIVITY_FILE,
    TOKENS_FILE,
    USERS_FILE,
    FakeAdminApi,
    FakeApiError,
)

DRIVE = "https://www.googleapis.com/auth/drive"
MAIL = "https://mail.google.com/"
CAL = "https://www.googleapis.com/auth/calendar.readonly"
NOTES = "000000000101-fakenotes.apps.googleusercontent.com"
WRITER = "000000000102-fakewriter.apps.googleusercontent.com"
USERS = [f"user{n}@meridian.example" for n in range(1, 6)]


def token(user: str, client: str, name: str, *scopes: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "admin#directory#token",
        "etag": '"fake"',
        "clientId": client,
        "displayText": name,
        "scopes": list(scopes),
        "userKey": user,
        "anonymous": False,
        "nativeApp": False,
        **extra,
    }


def activity(user: str, client: str, when: str, name: str = "activity") -> dict[str, Any]:
    return {
        "kind": "admin#reports#activity",
        "id": {"time": when, "applicationName": "token", "uniqueQualifier": f"{user}-{when}"},
        "actor": {"email": user},
        "events": [
            {
                "type": "auth",
                "name": name,
                "parameters": [
                    {"name": "client_id", "value": client},
                    {"name": "app_name", "value": "ignored"},
                ],
            }
        ],
    }


def world() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    users = [
        {"kind": "admin#directory#user", "primaryEmail": u, "id": str(i)}
        for i, u in enumerate(USERS)
    ]
    tokens = {
        USERS[0]: [
            token(USERS[0], NOTES, "Fake Notes AI", DRIVE, "openid", CAL),
            token(USERS[0], WRITER, "Fake Writer", MAIL),
        ],
        USERS[2]: [token(USERS[2], NOTES, "Fake Notes AI", CAL, DRIVE)],
        USERS[4]: [token(USERS[4], WRITER, "Fake Writer", MAIL, nativeApp=True)],
    }
    activities = [
        activity(USERS[0], NOTES, "2026-11-02T09:00:00.000Z"),
        activity(USERS[0], NOTES, "2026-11-12T10:30:00.000Z"),
        activity(USERS[0], WRITER, "2026-06-01T08:00:00+02:00"),
        activity(USERS[2], NOTES, "2026-10-01T12:00:00Z"),
        # An app the user no longer holds a token for is ignored.
        activity(USERS[3], WRITER, "2026-11-01T12:00:00Z"),
    ]
    return users, tokens, activities


def api(page_size: int = 2) -> FakeAdminApi:
    users, tokens, activities = world()
    return FakeAdminApi(users, tokens, activities, page_size=page_size)


def test_the_collector_returns_typed_grants_with_last_used() -> None:
    got = collect(api())
    assert isinstance(got, Collection)
    assert got.users == tuple(sorted(USERS))
    assert [g.key for g in got.grants] == [
        (USERS[0], NOTES),
        (USERS[0], WRITER),
        (USERS[2], NOTES),
        (USERS[4], WRITER),
    ]
    first = got.grants[0]
    assert first == TokenGrant(
        user_key=USERS[0],
        client_id=NOTES,
        display_text="Fake Notes AI",
        scopes=tuple(sorted([DRIVE, "openid", CAL])),
        anonymous=False,
        native_app=False,
        last_used=datetime(2026, 11, 12, 10, 30, tzinfo=UTC),
    )
    # Times are normalised to UTC; a grant with no activity has no last-used time.
    assert got.grants[1].last_used == datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
    assert got.grants[3].last_used is None
    assert got.grants[3].native_app is True


def test_last_used_counts_only_use_after_the_grant() -> None:
    users, tokens, _ = world()
    activities = [
        # USERS[0] authorized NOTES and used it once, later; WRITER was only authorized.
        activity(USERS[0], NOTES, "2026-09-01T09:00:00Z", name="authorize"),
        activity(USERS[0], NOTES, "2026-10-03T09:00:00Z"),
        activity(USERS[0], WRITER, "2026-11-10T09:00:00Z", name="authorize"),
        # A newer authorize (the user consented again) isn't use either.
        activity(USERS[2], NOTES, "2026-08-01T09:00:00Z"),
        activity(USERS[2], NOTES, "2026-11-01T09:00:00Z", name="authorize"),
        activity(USERS[4], WRITER, "2026-11-11T09:00:00Z", name="revoke"),
    ]
    got = {g.key: g.last_used for g in collect(FakeAdminApi(users, tokens, activities)).grants}
    assert got == {
        (USERS[0], NOTES): datetime(2026, 10, 3, 9, tzinfo=UTC),
        (USERS[0], WRITER): None,
        (USERS[2], NOTES): datetime(2026, 8, 1, 9, tzinfo=UTC),
        (USERS[4], WRITER): None,
    }


def test_an_event_needs_a_name() -> None:
    users, tokens, activities = world()
    del activities[0]["events"][0]["name"]
    with pytest.raises(CollectorError, match=r'activities.list page 0: item \d+: "name" must be'):
        collect(FakeAdminApi(users, tokens, activities))


def test_it_pages_through_every_user_and_every_activity_page() -> None:
    fake = api(page_size=2)
    collect(fake)
    names = [name for name, _ in fake.calls]
    assert names.count("users_list") == 3  # 5 users, 2 a page
    assert names.count("activities_list") == 3  # 5 activities, 2 a page
    assert sorted(arg for name, arg in fake.calls if name == "tokens_list") == sorted(USERS)


def test_page_size_doesnt_change_the_result() -> None:
    assert collect(api(page_size=1)) == collect(api(page_size=2)) == collect(api(page_size=100))


def test_the_same_data_in_any_order_gives_the_same_collection() -> None:
    users, tokens, activities = world()
    reordered = FakeAdminApi(
        list(reversed(users)),
        {key: list(reversed(items)) for key, items in reversed(list(tokens.items()))},
        list(reversed(activities)),
        page_size=3,
    )
    assert collect(reordered) == collect(api())


def test_no_socket_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the collector tried to open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert len(collect(api()).grants) == 4


def test_the_fake_has_no_call_that_changes_anything() -> None:
    public = {
        name
        for name, member in inspect.getmembers(FakeAdminApi)
        if callable(member) and not name.startswith("_")
    }
    assert public == {"users_list", "tokens_list", "activities_list", "from_files"}


def test_extra_fields_never_reach_a_record() -> None:
    names = {f.name for f in dataclasses.fields(TokenGrant)}
    assert names == {
        "user_key",
        "client_id",
        "display_text",
        "scopes",
        "anonymous",
        "native_app",
        "last_used",
    }
    users, tokens, activities = world()
    tokens[USERS[2]][0]["body"] = "secret message text"
    got = collect(FakeAdminApi(users, tokens, activities))
    assert "secret" not in repr(got)


def test_pages_are_copies() -> None:
    fake = api()
    page = fake.tokens_list(user_key=USERS[0])
    page["items"][0]["scopes"].append("https://www.googleapis.com/auth/admin.directory.user")
    assert collect(fake) == collect(api())


def test_a_user_with_no_tokens_gets_no_items_key() -> None:
    assert api().tokens_list(user_key=USERS[1]) == {"kind": "admin#directory#tokenList"}


def test_the_fake_refuses_what_the_real_api_would() -> None:
    fake = api()
    with pytest.raises(FakeApiError, match="unknown user"):
        fake.tokens_list(user_key="nobody@meridian.example")
    with pytest.raises(FakeApiError, match="page token"):
        fake.users_list(page_token="page:99")  # noqa: S106 (a page token, not a secret)
    with pytest.raises(FakeApiError, match="maxResults"):
        fake.users_list(max_results=0)
    with pytest.raises(FakeApiError, match="token"):
        fake.activities_list(application_name="drive")


class _Broken(FakeAdminApi):
    def __init__(self, change: str) -> None:
        super().__init__(*world(), page_size=2)
        self.change = change

    def users_list(self, **kwargs: Any) -> dict[str, Any]:
        page = super().users_list(**kwargs)
        if self.change == "repeat":
            page["nextPageToken"] = "page:2"
        return page

    def tokens_list(self, *, user_key: str) -> dict[str, Any]:
        page = super().tokens_list(user_key=user_key)
        if self.change == "scopes" and "items" in page:
            page["items"][0]["scopes"] = "drive"
        if self.change == "kind":
            page["kind"] = "admin#directory#token"
        return page

    def activities_list(self, **kwargs: Any) -> dict[str, Any]:
        page = super().activities_list(**kwargs)
        if self.change == "time" and page["items"]:
            page["items"][0]["id"]["time"] = "2026-11-02 09:00"
        return page


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("repeat", "repeated nextPageToken"),
        ("scopes", f'tokens.list for {USERS[0]}: item 0: "scopes"'),
        ("kind", f"tokens.list for {USERS[0]}: not a"),
        ("time", 'activities.list page 0: item 0: "id.time" has no time zone'),
    ],
)
def test_a_bad_page_stops_the_collection_and_says_where(change: str, message: str) -> None:
    with pytest.raises(CollectorError, match=message.replace("(", r"\(")):
        collect(_Broken(change))


def test_from_files_reads_what_the_dataset_writes(tmp_path: Path) -> None:
    users, tokens, activities = world()
    (tmp_path / USERS_FILE).write_text(json.dumps(users), encoding="utf-8")
    (tmp_path / TOKENS_FILE).write_text(json.dumps(tokens), encoding="utf-8")
    (tmp_path / ACTIVITY_FILE).write_text(json.dumps(activities), encoding="utf-8")
    assert collect(FakeAdminApi.from_files(tmp_path)) == collect(api())


def test_tokens_for_an_unknown_user_are_refused() -> None:
    users, tokens, activities = world()
    tokens["ghost@meridian.example"] = []
    with pytest.raises(ValueError, match="doesn't list"):
        FakeAdminApi(users, tokens, activities)
