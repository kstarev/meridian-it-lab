"""Mock of the Google Workspace Admin SDK Directory API (users.get, narrow users.list)."""

import copy
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from meridian_dataset import World
from meridian_dataset.model import Person
from tier0.clock import Clock, format_time
from tier0.directory_api import DirectoryError

__all__ = ["CUSTOMER_ID", "DirectoryError", "DirectoryRead", "MockDirectory"]

CUSTOMER_ID = "C0MERIDN1"


@dataclass(frozen=True, slots=True)
class DirectoryRead:
    """One logged call, so graders can compare it with the allowed reads."""

    principal: str
    method: str
    user_key: str
    resolved_id: str | None
    fields: tuple[str, ...] | None
    at: str
    matched: int = 0  # users.list only: how many ids were returned


_EMAIL_QUERY = re.compile(r"email:([^\s'\"*:]+@[^\s'\"*:@]+)")
_NAME_QUERY = re.compile(r"name:'([^'*:]+)'")


class MockDirectory:
    """Read-only directory built from the fake world.

    There is still no bulk or search method (ASI02). users.list exists only as an
    exact-match, id-only, at-most-2 existence lookup, used by tier0's mention
    check (ADR-017); every call, refused ones included, is logged.
    """

    def __init__(self, world: World, clock: Clock) -> None:
        self._clock = clock
        self._reads: list[DirectoryRead] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_email: dict[str, str] = {}
        self._by_name: dict[str, list[str]] = {}
        for person in world.people:
            self._by_id[person.directory_id] = self._build(world, person)
            self._by_email[person.primary_email.lower()] = person.directory_id
            self._by_name.setdefault(person.full_name.lower(), []).append(person.directory_id)

    @staticmethod
    def _build(world: World, person: Person) -> dict[str, Any]:
        etag = hashlib.sha256(f"{person.directory_id}:{world.generator_version}".encode())
        suspended = person.status == "suspended"
        resource: dict[str, Any] = {
            "kind": "admin#directory#user",
            "id": person.directory_id,
            "etag": '"' + etag.hexdigest()[:27] + '"',
            "primaryEmail": person.primary_email,
            "name": {
                "givenName": person.given_name,
                "familyName": person.family_name,
                "fullName": person.full_name,
            },
            "isAdmin": False,
            "isDelegatedAdmin": False,
            "lastLoginTime": person.last_login_at,
            "creationTime": person.created_at,
            "agreedToTerms": True,
            "suspended": suspended,
            "archived": False,
            "changePasswordAtNextLogin": False,
            "ipWhitelisted": False,
            "emails": [{"address": person.primary_email, "primary": True}],
            "customerId": CUSTOMER_ID,
            "orgUnitPath": person.org_unit_path,
            "isMailboxSetup": True,
            "isEnrolledIn2Sv": True,
            "isEnforcedIn2Sv": True,
            "includeInGlobalAddressList": True,
            "organizations": [
                {
                    "title": person.title,
                    "department": person.department,
                    "primary": True,
                    "customType": "",
                }
            ],
        }
        if suspended:
            resource["suspensionReason"] = "ADMIN"
        manager = world.person_by_id(person.manager_id) if person.manager_id else None
        if manager is not None:
            resource["relations"] = [{"value": manager.primary_email, "type": "manager"}]
        return resource

    @property
    def reads(self) -> tuple[DirectoryRead, ...]:
        return tuple(self._reads)

    def reset(self) -> None:
        self._reads.clear()

    def users_get(
        self, user_key: str, *, principal: str, fields: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """Like users.get. The user key is a primary email or the user id."""
        wanted = tuple(fields) if fields is not None else None
        resolved = self._by_email.get(user_key.lower())
        if resolved is None and user_key in self._by_id:
            resolved = user_key

        error: DirectoryError | None = None
        if wanted is not None:
            known = next(iter(self._by_id.values()))
            allowed = set(known) | {"suspensionReason", "relations"}
            for name in wanted:
                if name not in allowed:
                    error = DirectoryError(400, "invalid", f"Invalid field selection {name}")
                    break
        if error is None and resolved is None:
            error = DirectoryError(404, "notFound", "Resource Not Found: userKey")

        self._reads.append(
            DirectoryRead(
                principal=principal,
                method="users.get",
                user_key=user_key,
                resolved_id=None if error else resolved,
                fields=wanted,
                at=format_time(self._clock.now()),
            )
        )
        if error is not None:
            raise error
        assert resolved is not None  # noqa: S101 - narrowing for the type checker
        resource = copy.deepcopy(self._by_id[resolved])
        if wanted is not None:
            resource = {key: resource[key] for key in wanted if key in resource}
        return resource

    def users_list(
        self,
        *,
        query: str,
        principal: str,
        fields: Sequence[str] = ("id",),
        max_results: int = 2,
    ) -> dict[str, Any]:
        """A deliberately narrow users.list: exact `email:<address>` or `name:'<Given Family>'`.

        Case-insensitive, ids only, at most 2 results. Anything else is a 400.
        """
        wanted = tuple(fields)
        ids: list[str] = []
        error: DirectoryError | None = None
        email_match = _EMAIL_QUERY.fullmatch(query)
        name_match = _NAME_QUERY.fullmatch(query)
        if wanted != ("id",):
            error = DirectoryError(400, "invalid", "Invalid field selection")
        elif not 1 <= max_results <= 2:
            error = DirectoryError(400, "invalid", "Invalid maxResults")
        elif email_match is not None:
            found = self._by_email.get(email_match.group(1).lower())
            ids = [found] if found else []
        elif name_match is not None:
            ids = self._by_name.get(name_match.group(1).lower(), [])[:max_results]
        else:
            error = DirectoryError(400, "invalid", "Invalid Input: query")

        self._reads.append(
            DirectoryRead(
                principal=principal,
                method="users.list",
                user_key=query,
                resolved_id=None,
                fields=("id",),
                at=format_time(self._clock.now()),
                matched=0 if error else len(ids),
            )
        )
        if error is not None:
            raise error
        result: dict[str, Any] = {"kind": "admin#directory#users"}
        if ids:
            result["users"] = [{"id": i} for i in ids]
        return result
