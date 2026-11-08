from typing import Any

import pytest

from meridian_dataset import World
from tier0.clock import MockClock
from tier0.mocks.directory import CUSTOMER_ID, DirectoryError, MockDirectory

BASE_KEYS = {
    "kind", "id", "etag", "primaryEmail", "name", "isAdmin", "isDelegatedAdmin",
    "lastLoginTime", "creationTime", "agreedToTerms", "suspended", "archived",
    "changePasswordAtNextLogin", "ipWhitelisted", "emails", "customerId", "orgUnitPath",
    "isMailboxSetup", "isEnrolledIn2Sv", "isEnforcedIn2Sv", "includeInGlobalAddressList",
    "organizations",
}  # fmt: skip


def _first(world: World, *, suspended: bool, manager: bool) -> Any:
    return next(
        p
        for p in world.people
        if (p.status == "suspended") == suspended and (p.manager_id is not None) == manager
    )


def test_get_by_email_upper_and_id_match(world: World, directory: MockDirectory) -> None:
    person = _first(world, suspended=False, manager=True)
    by_email = directory.users_get(person.primary_email, principal="t")
    by_upper = directory.users_get(person.primary_email.upper(), principal="t")
    by_id = directory.users_get(person.directory_id, principal="t")
    assert by_email == by_upper == by_id
    assert set(by_email) == BASE_KEYS | {"relations"}
    assert by_email["customerId"] == CUSTOMER_ID


def test_suspended_has_reason_active_does_not(world: World, directory: MockDirectory) -> None:
    leaver = _first(world, suspended=True, manager=True)
    got = directory.users_get(leaver.primary_email, principal="t")
    assert got["suspended"] is True
    assert got["suspensionReason"] == "ADMIN"
    for person in world.people:
        if person.status == "active":
            res = directory.users_get(person.directory_id, principal="t")
            assert "suspensionReason" not in res
            assert res["suspended"] is False


def test_relations_point_to_manager(world: World, directory: MockDirectory) -> None:
    for person in world.people:
        res = directory.users_get(person.directory_id, principal="t")
        if person.manager_id is None:
            assert "relations" not in res
        else:
            manager = world.person_by_id(person.manager_id)
            assert manager is not None
            assert res["relations"] == [{"value": manager.primary_email, "type": "manager"}]
    assert any(p.manager_id is None for p in world.people)


def test_not_found(directory: MockDirectory) -> None:
    with pytest.raises(DirectoryError) as info:
        directory.users_get("nobody@meridian.example", principal="t")
    assert info.value.code == 404
    assert info.value.body == {
        "error": {
            "code": 404,
            "message": "Resource Not Found: userKey",
            "errors": [
                {
                    "domain": "global",
                    "reason": "notFound",
                    "message": "Resource Not Found: userKey",
                }
            ],
        }
    }


def test_fields_filter(world: World, directory: MockDirectory) -> None:
    person = _first(world, suspended=False, manager=True)
    res = directory.users_get(
        person.primary_email, principal="t", fields=["id", "suspended", "suspensionReason"]
    )
    assert set(res) == {"id", "suspended"}


def test_unknown_field(world: World, directory: MockDirectory) -> None:
    with pytest.raises(DirectoryError) as info:
        directory.users_get(world.people[0].primary_email, principal="t", fields=["bogus"])
    assert info.value.code == 400
    assert info.value.reason == "invalid"
    assert info.value.message == "Invalid field selection bogus"


def test_every_call_is_logged(world: World, directory: MockDirectory, clock: MockClock) -> None:
    person = world.people[0]
    directory.users_get(person.primary_email, principal="a", fields=["id"])
    with pytest.raises(DirectoryError):
        directory.users_get("nobody@meridian.example", principal="b")
    with pytest.raises(DirectoryError):
        directory.users_get(person.directory_id, principal="c", fields=["bogus"])
    first, second, third = directory.reads
    assert (first.principal, first.method, first.user_key) == (
        "a",
        "users.get",
        person.primary_email,
    )
    assert first.resolved_id == person.directory_id
    assert first.fields == ("id",)
    assert first.at == "2026-11-02T09:00:00.000Z"
    assert (second.principal, second.resolved_id, second.fields) == ("b", None, None)
    assert (third.principal, third.resolved_id, third.fields) == ("c", None, ("bogus",))


def test_returned_dicts_are_copies(world: World, directory: MockDirectory) -> None:
    key = world.people[0].primary_email
    first = directory.users_get(key, principal="t")
    first["name"]["givenName"] = "Mallory"
    first["suspended"] = True
    second = directory.users_get(key, principal="t")
    assert second["name"]["givenName"] == world.people[0].given_name
    assert second["suspended"] == (world.people[0].status == "suspended")


def test_reset_and_no_bulk_method(world: World, directory: MockDirectory) -> None:
    directory.users_get(world.people[0].primary_email, principal="t")
    directory.reset()
    assert directory.reads == ()
    assert not hasattr(MockDirectory, "users_list")
