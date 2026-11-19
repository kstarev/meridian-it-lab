"""A mock Google Admin token API: an in-process fake client, with no network at all.

The catalogue's collector "pulls grants from a mock Google Admin token API: app, scopes,
user, last used". This is that mock. It answers three read-only calls, each shaped like the
real response for the fields the radar reads (not every field the real API returns):

- **Directory API `users.list`:** `{"kind": "admin#directory#users", "users": [...],
  "nextPageToken": ...}`, one page at a time. Each user has `primaryEmail`.
- **Directory API `tokens.list`** for one user: `{"kind": "admin#directory#tokenList",
  "items": [...]}`. Each item has `clientId`, `displayText`, `scopes`, `userKey`,
  `anonymous` and `nativeApp`. As in the real API, this call isn't paged, and `items` is left
  out when the user has no tokens.
- **Reports API `activities.list`** for the `token` application: `{"kind":
  "admin#reports#activities", "items": [...], "nextPageToken": ...}`, newest first. Each item
  has `id.time`, `actor.email` and one event whose parameters name the `client_id`. This is
  the mock activity source for "last used". Event names are simplified.

**It changes nothing.** The real tokens resource can also take a token away. This fake has
no such method, on purpose: the radar only plans, and a person applies a plan by hand
(OAR-004, and the catalogue's "plan before apply"). It holds no credentials and opens no
socket; every page is a deep copy of data held in memory.

`FakeAdminApi.from_files` builds one from three JSON files, as the shared dataset writes
them (milestone 1, piece 2):

- `directory_users.json`: a list of user objects (`primaryEmail`, ...);
- `tokens.json`: `{primaryEmail: [token items]}`;
- `token_activity.json`: a list of activity items.

All data is fake.
"""

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

USERS_KIND = "admin#directory#users"
TOKENS_KIND = "admin#directory#tokenList"
ACTIVITIES_KIND = "admin#reports#activities"
AUDIT_APPLICATION = "token"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

USERS_FILE = "directory_users.json"
TOKENS_FILE = "tokens.json"
ACTIVITY_FILE = "token_activity.json"


class FakeApiError(LookupError):
    """The fake refused a call, as the real API would (unknown user, bad page token)."""


def _page_size(max_results: int | None, default: int) -> int:
    if max_results is None:
        return default
    if not 1 <= max_results <= MAX_PAGE_SIZE:
        raise FakeApiError(f"maxResults must be 1 to {MAX_PAGE_SIZE}, not {max_results}")
    return max_results


def _offset(page_token: str | None, length: int) -> int:
    if page_token is None:
        return 0
    prefix, _, number = page_token.partition(":")
    if prefix != "page" or not number.isdigit() or not 0 < int(number) < length:
        raise FakeApiError(f"unknown page token {page_token!r}")
    return int(number)


class FakeAdminApi:
    """In-memory Admin SDK stand-in: `users_list`, `tokens_list` and `activities_list` only.

    `calls` records every call as (method, argument), so a test can count them.
    """

    def __init__(
        self,
        users: Sequence[Mapping[str, Any]],
        tokens: Mapping[str, Sequence[Mapping[str, Any]]],
        activities: Sequence[Mapping[str, Any]] = (),
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._users = [copy.deepcopy(dict(user)) for user in users]
        emails = [user.get("primaryEmail") for user in self._users]
        if len(set(emails)) != len(emails):
            raise ValueError("every user needs a unique primaryEmail")
        unknown = sorted(set(tokens) - set(emails))
        if unknown:
            raise ValueError(f"tokens for users the directory doesn't list: {unknown}")
        self._tokens = {
            key: [copy.deepcopy(dict(item)) for item in items] for key, items in tokens.items()
        }
        # The Reports API returns the newest activity first.
        self._activities = sorted(
            (copy.deepcopy(dict(item)) for item in activities),
            key=lambda item: item.get("id", {}).get("time", ""),
            reverse=True,
        )
        self._page_size = _page_size(page_size, DEFAULT_PAGE_SIZE)
        self.calls: list[tuple[str, str]] = []

    @classmethod
    def from_files(cls, directory: Path, *, page_size: int = DEFAULT_PAGE_SIZE) -> "FakeAdminApi":
        """Build the fake from the three JSON files the shared dataset writes."""

        def read(name: str) -> Any:
            return json.loads((directory / name).read_text(encoding="utf-8"))

        return cls(read(USERS_FILE), read(TOKENS_FILE), read(ACTIVITY_FILE), page_size=page_size)

    def users_list(
        self,
        *,
        customer: str = "my_customer",
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """One page of the directory's users, like `users.list`."""
        self.calls.append(("users_list", page_token or ""))
        if customer != "my_customer":
            raise FakeApiError(f"unknown customer {customer!r}")
        size = _page_size(max_results, self._page_size)
        start = _offset(page_token, len(self._users))
        page: dict[str, Any] = {
            "kind": USERS_KIND,
            "users": copy.deepcopy(self._users[start : start + size]),
        }
        if start + size < len(self._users):
            page["nextPageToken"] = f"page:{start + size}"
        return page

    def tokens_list(self, *, user_key: str) -> dict[str, Any]:
        """Every token one user has issued to third-party apps, like `tokens.list`."""
        self.calls.append(("tokens_list", user_key))
        if user_key not in {user.get("primaryEmail") for user in self._users}:
            raise FakeApiError(f"unknown user {user_key!r}")
        response: dict[str, Any] = {"kind": TOKENS_KIND}
        items = self._tokens.get(user_key, [])
        if items:
            response["items"] = copy.deepcopy(items)
        return response

    def activities_list(
        self,
        *,
        user_key: str = "all",
        application_name: str = AUDIT_APPLICATION,
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """One page of token activity, newest first, like the Reports API's `activities.list`."""
        self.calls.append(("activities_list", page_token or ""))
        if application_name != AUDIT_APPLICATION:
            raise FakeApiError(f"this fake only knows the {AUDIT_APPLICATION!r} application")
        items = [
            item
            for item in self._activities
            if user_key == "all" or item.get("actor", {}).get("email") == user_key
        ]
        size = _page_size(max_results, self._page_size)
        start = _offset(page_token, len(items))
        page: dict[str, Any] = {
            "kind": ACTIVITIES_KIND,
            "items": copy.deepcopy(items[start : start + size]),
        }
        if start + size < len(items):
            page["nextPageToken"] = f"page:{start + size}"
        return page


__all__ = [
    "ACTIVITIES_KIND",
    "ACTIVITY_FILE",
    "TOKENS_FILE",
    "TOKENS_KIND",
    "USERS_FILE",
    "USERS_KIND",
    "FakeAdminApi",
    "FakeApiError",
]
