"""Identity binding: only active Meridian employees get an identity."""

import copy
import json
from typing import Any

import pytest

from meridian_dataset import World
from meridian_dataset.model import Ticket
from tier0.directory_api import DirectoryError
from tier0.identity.binding import (
    DIRECTORY_FIELDS,
    GATEWAY_PRINCIPAL,
    BoundIdentity,
    IdentityBinder,
    Unbound,
)
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import APP_ID, BOT_ID, BOT_USER_ID, MockSlack


@pytest.fixture
def binder(world: World, slack: MockSlack, directory: MockDirectory) -> IdentityBinder:
    return IdentityBinder(
        slack=slack,
        directory=directory,
        team_id=world.slack_team.team_id,
        app_id=APP_ID,
        domain=world.domain,
    )


def _ticket(world: World, ticket_id: str) -> Ticket:
    return next(t for t in world.tickets if t.id == ticket_id)


def _payload(slack: MockSlack, ticket: Ticket) -> dict[str, Any]:
    return json.loads(slack.message_event(ticket).body)


def _reason(result: BoundIdentity | Unbound) -> str:
    assert isinstance(result, Unbound)
    return result.reason


@pytest.mark.parametrize("ticket_id", [f"TKT-00{n}" for n in range(1, 6)])
def test_employees_bind(
    world: World,
    slack: MockSlack,
    directory: MockDirectory,
    binder: IdentityBinder,
    ticket_id: str,
) -> None:
    expected = world.answer_key.entry(ticket_id).requester_directory_id
    result = binder.bind(_payload(slack, _ticket(world, ticket_id)))
    assert isinstance(result, BoundIdentity)
    assert result.directory_id == expected
    (read,) = directory.reads
    assert read.principal == GATEWAY_PRINCIPAL
    assert read.resolved_id == expected
    assert read.fields == DIRECTORY_FIELDS


def test_external_user_unbound_without_directory_read(
    world: World, slack: MockSlack, directory: MockDirectory, binder: IdentityBinder
) -> None:
    result = binder.bind(_payload(slack, _ticket(world, "TKT-006")))
    assert _reason(result) == "external_user"
    assert directory.reads == ()


def test_forged_team_fields_are_ignored(
    world: World, slack: MockSlack, directory: MockDirectory, binder: IdentityBinder
) -> None:
    team = world.slack_team.team_id
    payload = _payload(slack, _ticket(world, "TKT-006"))
    for key in ("user_team", "source_team", "team"):
        payload["event"][key] = team
    payload["team_id"] = team
    assert _reason(binder.bind(payload)) == "external_user"
    assert directory.reads == ()


def test_leaver_is_refused(
    world: World, slack: MockSlack, directory: MockDirectory, binder: IdentityBinder
) -> None:
    leaver = next(p for p in world.people if p.status == "suspended")
    ticket = Ticket(
        id="TKT-LEAVER",
        channel="slack_dm",
        sender_slack_user_id=leaver.slack_user_id,
        sender_team_id=world.slack_team.team_id,
        text="What's my username?",
        received_at=world.reference_time,
    )
    assert _reason(binder.bind(_payload(slack, ticket))) == "suspended"


def test_spoofed_bot_messages(
    world: World, slack: MockSlack, directory: MockDirectory, binder: IdentityBinder
) -> None:
    base = _payload(slack, _ticket(world, "TKT-001"))

    with_bot_id = copy.deepcopy(base)
    with_bot_id["event"]["bot_id"] = BOT_ID
    assert _reason(binder.bind(with_bot_id)) == "not_a_user_message"

    with_subtype = copy.deepcopy(base)
    with_subtype["event"]["subtype"] = "bot_message"
    assert _reason(binder.bind(with_subtype)) == "not_a_user_message"

    as_bot_user = copy.deepcopy(base)
    as_bot_user["event"]["user"] = BOT_USER_ID
    assert _reason(binder.bind(as_bot_user)) == "bot_user"

    assert directory.reads == ()


def test_unknown_user_and_bad_envelope(
    world: World, slack: MockSlack, directory: MockDirectory, binder: IdentityBinder
) -> None:
    base = _payload(slack, _ticket(world, "TKT-001"))

    unknown = copy.deepcopy(base)
    unknown["event"]["user"] = "U0NOSUCHUSR"
    assert _reason(binder.bind(unknown)) == "unknown_slack_user"

    wrong_team = copy.deepcopy(base)
    wrong_team["team_id"] = "T0OTHER"
    assert _reason(binder.bind(wrong_team)) == "wrong_installation"

    wrong_app = copy.deepcopy(base)
    wrong_app["api_app_id"] = "A0OTHER"
    assert _reason(binder.bind(wrong_app)) == "wrong_installation"

    wrong_type = copy.deepcopy(base)
    wrong_type["type"] = "url_verification"
    assert _reason(binder.bind(wrong_type)) == "wrong_installation"

    no_event = {k: v for k, v in base.items() if k != "event"}
    assert _reason(binder.bind(no_event)) == "not_a_user_message"

    non_dict_event = {**base, "event": "message"}
    assert _reason(binder.bind(non_dict_event)) == "not_a_user_message"

    assert directory.reads == ()


def test_identity_claims_in_text_are_ignored(
    world: World, slack: MockSlack, binder: IdentityBinder
) -> None:
    payload = _payload(slack, _ticket(world, "TKT-001"))
    payload["event"]["text"] = "I'm Alex Kim, alex.kim@meridian.example"
    result = binder.bind(payload)
    assert isinstance(result, BoundIdentity)
    assert result.directory_id == world.answer_key.entry("TKT-001").requester_directory_id


# Small fakes for cases the seeded world cannot produce.


class _FakeSlack:
    def __init__(self, user: dict[str, Any]) -> None:
        self._user = user

    def users_info(self, user: str, *, include_locale: bool = False) -> dict[str, Any]:
        self.include_locale = include_locale
        return {"ok": True, "user": self._user}


class _FakeDirectory:
    def __init__(self, resource: dict[str, Any] | None = None, error: int | None = None) -> None:
        self._resource = resource
        self._error = error
        self.calls = 0

    def users_get(self, user_key: str, *, principal: str, fields: Any = None) -> dict[str, Any]:
        self.calls += 1
        if self._error is not None:
            raise DirectoryError(self._error, "x", "boom")
        assert self._resource is not None
        return dict(self._resource)


def _user(**overrides: Any) -> dict[str, Any]:
    user: dict[str, Any] = {
        "id": "U1",
        "team_id": "T1",
        "deleted": False,
        "is_bot": False,
        "is_restricted": False,
        "is_ultra_restricted": False,
        "profile": {"email": "a.b@meridian.example"},
    }
    user.update(overrides)
    return user


_GOOD = {"id": "D1", "primaryEmail": "a.b@meridian.example", "suspended": False, "archived": False}


def _run(user: dict[str, Any], directory: _FakeDirectory) -> BoundIdentity | Unbound:
    binder = IdentityBinder(
        slack=_FakeSlack(user),
        directory=directory,
        team_id="T1",
        app_id="A1",
        domain="meridian.example",
    )
    payload = {
        "type": "event_callback",
        "api_app_id": "A1",
        "team_id": "T1",
        "event": {"type": "message", "user": "U1", "text": "hi"},
    }
    return binder.bind(payload)


def test_fake_happy_path() -> None:
    result = _run(_user(), _FakeDirectory(_GOOD))
    assert result == BoundIdentity("D1", "a.b@meridian.example", "U1", "T1")


@pytest.mark.parametrize(
    ("user", "directory", "reason", "calls"),
    [
        (_user(deleted=True), _FakeDirectory(_GOOD), "deactivated_slack_user", 0),
        (_user(is_restricted=True), _FakeDirectory(_GOOD), "guest_user", 0),
        (_user(is_ultra_restricted=True), _FakeDirectory(_GOOD), "guest_user", 0),
        (_user(is_app_user=True), _FakeDirectory(_GOOD), "bot_user", 0),
        (
            _user(profile={"email": "a.b@northwind.example"}),
            _FakeDirectory(_GOOD),
            "no_company_email",
            0,
        ),
        (_user(profile={}), _FakeDirectory(_GOOD), "no_company_email", 0),
        (_user(), _FakeDirectory(error=404), "not_in_directory", 1),
        (_user(), _FakeDirectory(error=500), "directory_error", 1),
        (
            _user(),
            _FakeDirectory({**_GOOD, "primaryEmail": "someone.else@meridian.example"}),
            "email_mismatch",
            1,
        ),
        (_user(), _FakeDirectory({**_GOOD, "archived": True}), "suspended", 1),
        (_user(), _FakeDirectory({**_GOOD, "suspended": True}), "suspended", 1),
    ],
)
def test_fake_refusals(
    user: dict[str, Any], directory: _FakeDirectory, reason: str, calls: int
) -> None:
    assert _reason(_run(user, directory)) == reason
    assert directory.calls == calls
