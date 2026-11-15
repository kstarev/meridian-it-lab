"""Collectors: read Okta, Google, Slack and the CSV exports into normalised Accounts.

A collector is given a read-only token and only ever issues GETs. `HttpishClient` follows
paging and retries `429` (honouring `Retry-After`) and `5xx` with a capped backoff.
Nothing here reads the answer key or any `true_roles`; that is for scoring code only.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from meridian_access.exports import ExportsResult, FileReport, parse_exports
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock, Token
from meridian_access.mocks import MockResponse as Response
from meridian_access.models import Account, CollectorError, LogEvent
from meridian_access.policy.load import Policy

__all__ = [
    "Account",
    "ActualState",
    "CollectorError",
    "ExportsResult",
    "FileReport",
    "HttpishClient",
    "LogEvent",
    "Mocks",
    "actual_state",
    "collect_google",
    "collect_okta",
    "collect_slack",
    "parse_exports",
]

MAX_BACKOFF_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 120.0
BASE_BACKOFF_SECONDS = 0.5
MAX_PAGES = 10_000  # a list that needs more pages than this is a loop, not data
_NEXT_LINK = re.compile(r'<([^>]*)>\s*;\s*rel="next"')


class HttpishClient:
    """Wraps one mock and one token. `request` retries 429 and 5xx, returns the final
    response (a 4xx other than 429 is returned as is), and raises CollectorError once
    `max_retries` retries are used up."""

    def __init__(
        self,
        mock: Any,
        token: Token,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 6,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._mock = mock
        self._token = token
        self._sleep = sleep
        self.max_retries = max_retries
        self.max_pages = max_pages
        self.retries = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Response:
        attempt = 0
        while True:
            response = self._mock.request(method, path, token=self._token, params=params, body=body)
            retryable = response.status == 429 or 500 <= response.status < 600
            if not retryable:
                return response
            if attempt >= self.max_retries:
                raise CollectorError(
                    f"{method} {path}: gave up after {self.max_retries} retries "
                    f"(last status {response.status})",
                    response.status,
                )
            self._sleep(self._wait(response, attempt))
            attempt += 1
            self.retries += 1

    @staticmethod
    def _wait(response: Response, attempt: int) -> float:
        if response.status == 429:
            try:
                wait = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            return min(max(wait, 0.0), MAX_RETRY_AFTER_SECONDS)
        return min(BASE_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET and return the JSON body; any non-2xx answer is a CollectorError."""
        response = self.request("GET", path, params=params)
        if not 200 <= response.status < 300:
            raise CollectorError(f"GET {path}: status {response.status}", response.status)
        return response.json

    def page_guard(self, path: str) -> Callable[[str, int], None]:
        """A checker for one paged read: call it with each page's next cursor and the page
        number; it raises CollectorError on a repeated cursor or past the page cap."""
        seen: set[str] = set()

        def check(cursor: str, page: int) -> None:
            if page > self.max_pages:
                raise CollectorError(f"GET {path}: more than {self.max_pages} pages")
            if cursor in seen:
                raise CollectorError(f"GET {path}: the cursor {cursor!r} came back (a paging loop)")
            seen.add(cursor)

        return check

    def okta_pages(self, path: str, params: Mapping[str, Any] | None = None) -> Iterator[Any]:
        """Items of an Okta list, following `Link: <...after=CURSOR>; rel="next"`. Stops on a
        page with no items; a repeated cursor or too many pages is a CollectorError."""
        query: dict[str, Any] = dict(params or {})
        guard = self.page_guard(path)
        pages = 0
        while True:
            response = self.request("GET", path, params=query)
            if not 200 <= response.status < 300:
                raise CollectorError(f"GET {path}: status {response.status}", response.status)
            pages += 1
            if not response.json:
                return
            yield from response.json
            match = _NEXT_LINK.search(response.headers.get("Link", ""))
            if not match:
                return
            after = re.search(r"[?&]after=([^&>]+)", match.group(1))
            if not after:
                raise CollectorError(f"GET {path}: a next link with no cursor", response.status)
            guard(after.group(1), pages)
            query["after"] = after.group(1)


# ----------------------------------------------------------------------- Okta


_OKTA_STATE = {"SUSPENDED": "suspended", "DEPROVISIONED": "deprovisioned"}


def collect_okta(client: HttpishClient) -> tuple[list[Account], list[LogEvent]]:
    """Every Okta user, one Account per (user, app) assignment, and the system log."""
    users = list(client.okta_pages("/api/v1/users", {"limit": 200}))
    apps = list(client.okta_pages("/api/v1/apps", {"limit": 200}))
    app_name = {a["id"]: a["name"] for a in apps}
    login_of = {u["id"]: u["profile"]["login"] for u in users}
    state_of = {u["id"]: _OKTA_STATE.get(u["status"], "active") for u in users}
    accounts: list[Account] = []
    for u in users:
        profile = u["profile"]
        accounts.append(
            Account(
                system="okta",
                account=profile["login"],
                email=profile.get("email", ""),
                given=profile.get("firstName", ""),
                family=profile.get("lastName", ""),
                display=f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
                state=state_of[u["id"]],  # type: ignore[arg-type]
                created=u.get("created", ""),
                last_login=u.get("lastLogin") or "",
                source_ref=u["id"],
            )
        )
    for app in apps:
        for au in client.okta_pages(f"/api/v1/apps/{app['id']}/users", {"limit": 200}):
            login = login_of.get(au["id"], au["id"])
            accounts.append(
                Account(
                    system="okta",
                    account=login,
                    email=login if "@" in login else "",
                    # a deprovisioned or suspended user's assignments are not active
                    state=state_of.get(au["id"], "active"),  # type: ignore[arg-type]
                    app_id=app["name"],
                    role=au.get("profile", {}).get("role", ""),
                    scope=au.get("scope", ""),
                    created=au.get("created", ""),
                    source_ref=f"{au['id']}@{app['id']}",
                )
            )
    events: list[LogEvent] = []
    for e in client.okta_pages("/api/v1/logs", {"limit": 200}):
        targets = e.get("target", [])
        app = next((t for t in targets if t.get("type") == "AppInstance"), {})
        user = next((t for t in targets if t.get("type") == "User"), {})
        events.append(
            LogEvent(
                uuid=e["uuid"],
                published=e["published"],
                event_type=e["eventType"],
                actor=e.get("actor", {}).get("alternateId", ""),
                actor_type=e.get("actor", {}).get("type", ""),
                via=e.get("transaction", {}).get("detail", {}).get("via", ""),
                app_id=app.get("id", ""),
                app=app_name.get(app.get("id", ""), ""),
                user_id=user.get("id", ""),
                user_login=user.get("alternateId", ""),
            )
        )
    return accounts, events


# --------------------------------------------------------------------- Google


def collect_google(client: HttpishClient, page_size: int = 500) -> list[Account]:
    accounts: list[Account] = []
    token = ""
    guard = client.page_guard("/admin/directory/v1/users")
    pages = 0
    while True:
        params: dict[str, Any] = {"customer": "my_customer", "maxResults": page_size}
        if token:
            params["pageToken"] = token
        page = client.get("/admin/directory/v1/users", params)
        for u in page.get("users", []):
            name = u.get("name", {})
            accounts.append(
                Account(
                    system="google",
                    account=u["primaryEmail"],
                    email=u["primaryEmail"],
                    given=name.get("givenName", ""),
                    family=name.get("familyName", ""),
                    display=name.get("fullName", ""),
                    state="suspended" if u.get("suspended") else "active",
                    created=u.get("creationTime", ""),
                    last_login=u.get("lastLoginTime", ""),
                    source_ref=u["id"],
                    aliases=tuple(u.get("aliases", ())),
                )
            )
        token = page.get("nextPageToken", "")
        if not token:
            return accounts
        pages += 1
        guard(token, pages)


# ---------------------------------------------------------------------- Slack


def collect_slack(client: HttpishClient) -> list[Account]:
    accounts: list[Account] = []
    start = 1
    guard = client.page_guard("/scim/v2/Users")
    pages = 0
    while True:
        pages += 1
        guard(str(start), pages)
        page = client.get("/scim/v2/Users", {"startIndex": start, "count": 200})
        resources = page.get("Resources", [])
        for u in resources:
            emails = u.get("emails", [])
            primary = next((e for e in emails if e.get("primary")), emails[0] if emails else {})
            accounts.append(
                Account(
                    system="slack",
                    account=u["userName"],
                    email=primary.get("value", ""),
                    display=u.get("displayName", ""),
                    state="active" if u.get("active") else "deprovisioned",
                    source_ref=u["id"],
                )
            )
        start += len(resources)
        if not resources or start > page.get("totalResults", 0):
            return accounts


# ---------------------------------------------------------------- actual state


@dataclass(frozen=True, slots=True)
class Mocks:
    okta: OktaMock
    google: GoogleMock
    slack: SlackMock


@dataclass(frozen=True, slots=True)
class ActualState:
    accounts: tuple[Account, ...]
    log: tuple[LogEvent, ...]
    exports_result: ExportsResult | None

    def digest(self) -> str:
        """sha256 of a canonical JSON dump of the sorted accounts and the Okta log (the plan
        records it)."""
        rows = sorted(
            (asdict(a) for a in self.accounts),
            key=lambda d: (d["system"], d["app_id"], d["account"], d["source_ref"]),
        )
        log = sorted(
            (asdict(e) for e in self.log),
            key=lambda d: (d["published"], d["uuid"], d["event_type"], d["user_login"]),
        )
        body = {"accounts": rows, "log": log}
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode()).hexdigest()


def actual_state(
    source: Path | str | Mocks,
    policy: Policy,
    exports_dir: Path | str | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    fault_rate: float = 0.0,
) -> ActualState:
    """What exists now. `source` is a world directory (okta.json, google.json,
    slack_scim.json and exports/) or ready-made mocks plus `exports_dir`. A read-only token
    is issued for each mock; collectors can't write."""
    if isinstance(source, Mocks):
        mocks = source
        exports = Path(exports_dir) if exports_dir is not None else None
    else:
        root = Path(source)
        mocks = Mocks(
            OktaMock.from_files(root, fault_rate=fault_rate),
            GoogleMock.from_files(root, fault_rate=fault_rate),
            SlackMock.from_files(root, fault_rate=fault_rate),
        )
        exports = Path(exports_dir) if exports_dir is not None else root / "exports"
    clients = {
        name: HttpishClient(mock, mock.issue_token(f"collector-{name}", {"read"}), sleep=sleep)
        for name, mock in (("okta", mocks.okta), ("google", mocks.google), ("slack", mocks.slack))
    }
    okta_accounts, log = collect_okta(clients["okta"])
    accounts = [
        *okta_accounts,
        *collect_google(clients["google"]),
        *collect_slack(clients["slack"]),
    ]
    result = None
    if exports is not None:
        result = parse_exports(exports, policy)
        accounts += result.accounts
    return ActualState(tuple(accounts), tuple(log), result)
