"""The collector: every user's OAuth grants, with when each was last used (milestone 1).

It pages through the directory's users, asks for each user's tokens, and pages through the
token activity to find when each grant was last used. It returns typed `TokenGrant` records,
sorted, so the same API data always gives the same tuple.

**"Last used" counts only use after the grant** (OAR-010, OAR-011): the `authorize` event
that made the grant isn't use, and neither is a `revoke`. Any other token event is use.

It talks only to an `AdminApi`: an object with the three read calls of the fake in
`meridian_oauth_radar.fake_admin`. There is no HTTP client in this package, no credential
and no call that changes anything (OAR-004, OAR-005). It reads only the fields it names;
anything else in a page never reaches a record.

A page that isn't the expected shape stops the collection with an error that says where.
Nothing is dropped silently, because a skipped grant would be a missed finding.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from meridian_oauth_radar.fake_admin import ACTIVITIES_KIND, TOKENS_KIND, USERS_KIND

# A guard against a page token that never ends. 230 users at 100 a page is 3 pages.
MAX_PAGES = 1000
# Token events that aren't use: the grant itself, and taking it back (OAR-011).
NOT_USE = frozenset({"authorize", "revoke"})


class AdminApi(Protocol):
    """The three read calls the collector makes. Nothing here can change a grant."""

    def users_list(
        self, *, customer: str = ..., max_results: int | None = ..., page_token: str | None = ...
    ) -> Mapping[str, Any]: ...

    def tokens_list(self, *, user_key: str) -> Mapping[str, Any]: ...

    def activities_list(
        self,
        *,
        user_key: str = ...,
        application_name: str = ...,
        max_results: int | None = ...,
        page_token: str | None = ...,
    ) -> Mapping[str, Any]: ...


class CollectorError(ValueError):
    """A page isn't the shape the collector reads. The message says which call and where."""


@dataclass(frozen=True, slots=True)
class TokenGrant:
    """One user's OAuth token for one app, as `tokens.list` shows it, plus when it was last used.

    `last_used` is the newest use of the grant by this user, in UTC: the newest token event
    that isn't `authorize` or `revoke`. It is None when the activity source has no use since
    the grant. `scopes` are sorted, with no duplicates. There is no field for
    message or file content, because the radar never reads any (OAR-005).
    """

    user_key: str
    client_id: str
    display_text: str
    scopes: tuple[str, ...]
    anonymous: bool
    native_app: bool
    last_used: datetime | None

    def __post_init__(self) -> None:
        if self.scopes != tuple(sorted(set(self.scopes))):
            raise ValueError("scopes must be sorted and unique")
        if self.last_used is not None and self.last_used.utcoffset() is None:
            raise ValueError("last_used must carry a time zone")

    @property
    def key(self) -> tuple[str, str]:
        """(user, app): one grant per pair."""
        return (self.user_key, self.client_id)


@dataclass(frozen=True, slots=True)
class Collection:
    """What one collection saw: every user it asked about, and every grant, sorted."""

    users: tuple[str, ...]
    grants: tuple[TokenGrant, ...]


def _text(where: str, item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectorError(f'{where}: "{key}" must be a non-empty string')
    return value


def _flag(where: str, item: Mapping[str, Any], key: str) -> bool:
    value = item.get(key, False)
    if not isinstance(value, bool):
        raise CollectorError(f'{where}: "{key}" must be true or false')
    return value


def _kind(where: str, page: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(page, Mapping) or page.get("kind") != kind:
        raise CollectorError(f'{where}: not a "{kind}" response')
    return page


def _next(where: str, page: Mapping[str, Any], seen: set[str]) -> str | None:
    token = page.get("nextPageToken")
    if token is None:
        return None
    if not isinstance(token, str) or not token or token in seen:
        raise CollectorError(f"{where}: a bad or repeated nextPageToken {token!r}")
    if len(seen) >= MAX_PAGES:
        raise CollectorError(f"{where}: more than {MAX_PAGES} pages")
    seen.add(token)
    return token


def _users(api: AdminApi) -> tuple[str, ...]:
    users: list[str] = []
    seen: set[str] = set()
    token: str | None = None
    page_number = 0
    while True:
        where = f"users.list page {page_number}"
        page = _kind(where, api.users_list(page_token=token), USERS_KIND)
        listed = page.get("users", [])
        if not isinstance(listed, list):
            raise CollectorError(f'{where}: "users" must be a list')
        for index, user in enumerate(listed):
            if not isinstance(user, Mapping):
                raise CollectorError(f"{where}: user {index}: not an object")
            users.append(_text(f"{where}: user {index}", user, "primaryEmail"))
        token = _next(where, page, seen)
        if token is None:
            break
        page_number += 1
    if len(set(users)) != len(users):
        raise CollectorError("users.list: a user is listed twice")
    return tuple(users)


def _time(where: str, value: Any) -> datetime:
    if not isinstance(value, str):
        raise CollectorError(f'{where}: "id.time" must be a string')
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as err:
        raise CollectorError(f'{where}: "id.time" is not a date and time ({value!r})') from err
    if moment.utcoffset() is None:
        raise CollectorError(f'{where}: "id.time" has no time zone ({value!r})')
    return moment.astimezone(UTC)


def _client_id(where: str, event: Mapping[str, Any]) -> str | None:
    parameters = event.get("parameters", [])
    if not isinstance(parameters, list):
        raise CollectorError(f'{where}: "parameters" must be a list')
    for parameter in parameters:
        if isinstance(parameter, Mapping) and parameter.get("name") == "client_id":
            return _text(where, parameter, "value")
    return None


def _last_used(api: AdminApi) -> dict[tuple[str, str], datetime]:
    """The newest use for every (user, app) the token activity mentions. A grant isn't use."""
    newest: dict[tuple[str, str], datetime] = {}
    seen: set[str] = set()
    token: str | None = None
    page_number = 0
    while True:
        where = f"activities.list page {page_number}"
        page = _kind(
            where,
            api.activities_list(user_key="all", application_name="token", page_token=token),
            ACTIVITIES_KIND,
        )
        items = page.get("items", [])
        if not isinstance(items, list):
            raise CollectorError(f'{where}: "items" must be a list')
        for index, item in enumerate(items):
            at = f"{where}: item {index}"
            if not isinstance(item, Mapping):
                raise CollectorError(f"{at}: not an object")
            ident, actor, events = item.get("id"), item.get("actor"), item.get("events")
            if not isinstance(ident, Mapping) or not isinstance(actor, Mapping):
                raise CollectorError(f'{at}: needs "id" and "actor" objects')
            if not isinstance(events, list):
                raise CollectorError(f'{at}: "events" must be a list')
            moment = _time(at, ident.get("time"))
            user = _text(at, actor, "email")
            for event in events:
                if not isinstance(event, Mapping):
                    raise CollectorError(f"{at}: an event is not an object")
                client = _client_id(at, event)
                if client is None or _text(at, event, "name") in NOT_USE:
                    continue
                key = (user, client)
                if key not in newest or moment > newest[key]:
                    newest[key] = moment
        token = _next(where, page, seen)
        if token is None:
            break
        page_number += 1
    return newest


def _grants(
    api: AdminApi, user: str, last_used: Mapping[tuple[str, str], datetime]
) -> list[TokenGrant]:
    where = f"tokens.list for {user}"
    response = _kind(where, api.tokens_list(user_key=user), TOKENS_KIND)
    # tokens.list leaves "items" out when the user has no tokens.
    items = response.get("items", [])
    if not isinstance(items, list):
        raise CollectorError(f'{where}: "items" must be a list')
    grants: list[TokenGrant] = []
    for index, item in enumerate(items):
        at = f"{where}: item {index}"
        if not isinstance(item, Mapping):
            raise CollectorError(f"{at}: not an object")
        user_key = _text(at, item, "userKey")
        if user_key != user:
            raise CollectorError(f"{at}: userKey {user_key!r} is not {user!r}")
        scopes = item.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(s, str) and s for s in scopes):
            raise CollectorError(f'{at}: "scopes" must be a list of non-empty strings')
        client_id = _text(at, item, "clientId")
        grants.append(
            TokenGrant(
                user_key=user_key,
                client_id=client_id,
                display_text=_text(at, item, "displayText"),
                scopes=tuple(sorted(set(scopes))),
                anonymous=_flag(at, item, "anonymous"),
                native_app=_flag(at, item, "nativeApp"),
                last_used=last_used.get((user_key, client_id)),
            )
        )
    if len({grant.client_id for grant in grants}) != len(grants):
        raise CollectorError(f"{where}: an app is listed twice")
    return grants


def collect(api: AdminApi) -> Collection:
    """Every user's grants, sorted by user and app, each with its last-used time."""
    users = _users(api)
    last_used = _last_used(api)
    grants = [grant for user in users for grant in _grants(api, user, last_used)]
    return Collection(users=tuple(sorted(users)), grants=tuple(sorted(grants, key=_order)))


def _order(grant: TokenGrant) -> tuple[str, str]:
    return grant.key


__all__ = [
    "MAX_PAGES",
    "NOT_USE",
    "AdminApi",
    "Collection",
    "CollectorError",
    "TokenGrant",
    "collect",
]
