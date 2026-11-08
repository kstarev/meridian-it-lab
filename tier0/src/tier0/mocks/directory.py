"""Mock of the Google Workspace Admin SDK Directory API (users.get only)."""

import copy
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from meridian_dataset import World
from meridian_dataset.model import Person
from tier0.clock import Clock, format_time

CUSTOMER_ID = "C0MERIDN1"


class DirectoryError(Exception):
    """An API error, carrying the same JSON body Google returns."""

    def __init__(self, code: int, reason: str, message: str) -> None:
        super().__init__(f"{code} {reason}: {message}")
        self.code = code
        self.reason = reason
        self.message = message

    @property
    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "errors": [{"domain": "global", "reason": self.reason, "message": self.message}],
            }
        }


@dataclass(frozen=True, slots=True)
class DirectoryRead:
    """One logged call, so graders can compare it with the allowed reads."""

    principal: str
    method: str
    user_key: str
    resolved_id: str | None
    fields: tuple[str, ...] | None
    at: str


class MockDirectory:
    """Read-only directory built from the fake world.

    There is deliberately no users.list or other bulk method: tier0 never needs
    one, and leaving it out removes a bulk-export path (ASI02).
    """

    def __init__(self, world: World, clock: Clock) -> None:
        self._clock = clock
        self._reads: list[DirectoryRead] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_email: dict[str, str] = {}
        for person in world.people:
            self._by_id[person.directory_id] = self._build(world, person)
            self._by_email[person.primary_email.lower()] = person.directory_id

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
