"""In-process mocks of Okta, the Google Directory API and Slack SCIM (AAC-003).

Each mock is stateful, resettable and seeded. Requests go through one method,
`request(method, path, token=..., params=..., body=...)`, and the answer is a
`MockResponse(status, headers, json)`. Field names follow the real APIs, as the raw
`okta.json`, `google.json` and `slack_scim.json` files already do.

Behaviour that matters is real: paging (Okta `Link` header cursors, Google `pageToken`,
Slack `startIndex`/`count`), a seeded share of `429` (with `Retry-After`) and transient
`503` answers, `401` for a token the mock never issued, `403` for a write with a read-only
token, `404` for an unknown path. The mocks only read the three raw systems; they never
see the HRIS or the answer key.
"""

import copy
import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

from meridian_dataset.access.model import AccessWorld

READ = frozenset({"read"})
READ_WRITE = frozenset({"read", "write"})
DEFAULT_PAGE_SIZE = 50
DEFAULT_FAULT_RATE = 0.05
SLACK_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SLACK_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SLACK_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
OKTA_BASE = "https://meridian.okta.example"
GOOGLE_BASE = "https://admin.googleapis.example"
SLACK_BASE = "https://api.slack.example"


@dataclass(frozen=True, slots=True)
class Token:
    name: str
    scopes: frozenset[str] = READ


@dataclass(frozen=True, slots=True)
class MockResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    json: Any = None


def _stable(data: Any) -> Any:
    return json.loads(json.dumps(data))


class _Mock:
    """Shared plumbing: tokens, seeded faults, routing. Subclasses add `_routes`."""

    name = "mock"
    base_url = ""

    def __init__(
        self,
        data: dict[str, Any],
        *,
        seed: int = 0,
        fault_rate: float = DEFAULT_FAULT_RATE,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        if not 0 <= fault_rate <= 1:
            raise ValueError("fault_rate must be between 0 and 1")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        self._initial = _stable(data)
        self.seed = seed
        self.fault_rate = fault_rate
        self.page_size = page_size
        self._tokens: set[Token] = set()
        self.request_count = 0
        self.reset()

    def reset(self) -> None:
        """Back to the initial data, the initial fault sequence and no calls counted.
        Issued tokens stay valid."""
        self.data = copy.deepcopy(self._initial)
        self._rng = random.Random(f"{self.name}:{self.seed}")  # noqa: S311 - a fault schedule, not a secret
        self.request_count = 0

    def issue_token(self, name: str, scopes: frozenset[str] | set[str] = READ) -> Token:
        token = Token(name, frozenset(scopes))
        self._tokens.add(token)
        return token

    def request(
        self,
        method: str,
        path: str,
        *,
        token: Token | None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> MockResponse:
        self.request_count += 1
        if token is None or token not in self._tokens:
            return MockResponse(401, {}, {"error": "invalid token"})
        # One draw per authorised request, whatever the route, so a fault sequence is a
        # function of the seed and the request count only.
        draw = self._rng.random()
        kind = self._rng.random()
        if draw < self.fault_rate:
            if kind < 0.5:
                return MockResponse(429, {"Retry-After": "1"}, {"error": "rate limited"})
            return MockResponse(503, {}, {"error": "service unavailable"})
        method = method.upper()
        for route_method, pattern, handler in self._routes():
            match = pattern.fullmatch(path)
            if match and route_method == method:
                if method != "GET" and "write" not in token.scopes:
                    return MockResponse(403, {}, {"error": "write scope required"})
                return handler(dict(params or {}), body, *match.groups())
        # A known path with another method is a 405 in real APIs; here it is a 404 too.
        return MockResponse(404, {}, {"error": f"no route {method} {path}"})

    def _routes(self) -> list[tuple[str, re.Pattern[str], Callable[..., MockResponse]]]:
        raise NotImplementedError

    def _limit(self, params: Mapping[str, Any], key: str, maximum: int = 1000) -> int:
        try:
            value = int(params.get(key, self.page_size))
        except (TypeError, ValueError):
            value = self.page_size
        return max(1, min(value, maximum))


def _route(method: str, pattern: str, handler: Callable[..., MockResponse]):
    return (method, re.compile(pattern), handler)


# ---------------------------------------------------------------------------- Okta


class OktaMock(_Mock):
    name = "okta"
    base_url = OKTA_BASE
    _LIFECYCLE: ClassVar[dict[str, str]] = {
        "deactivate": "DEPROVISIONED",
        "suspend": "SUSPENDED",
        "unsuspend": "ACTIVE",
    }

    @classmethod
    def from_files(cls, directory: Path, **kwargs: Any) -> "OktaMock":
        raw = json.loads((Path(directory) / "okta.json").read_text(encoding="utf-8"))
        return cls(raw, **kwargs)

    @classmethod
    def from_world(cls, world: AccessWorld, *, seed: int | None = None, **kwargs: Any):
        okta = world.okta
        data = {
            "users": [
                {
                    "id": u.id,
                    "status": u.status,
                    "created": u.created,
                    "lastLogin": u.last_login,
                    "profile": {
                        "login": u.login,
                        "email": u.email,
                        "firstName": u.first_name,
                        "lastName": u.last_name,
                    },
                }
                for u in okta.users
            ],
            "apps": [
                {"id": a.id, "label": a.label, "name": a.app_id, "status": "ACTIVE"}
                for a in okta.apps
            ],
            "assignments": [
                {
                    "appId": a.app_id,
                    "userId": a.user_id,
                    "created": a.created,
                    "scope": a.scope,
                    "profile": {"role": a.role},
                }
                for a in okta.assignments
            ],
            "system_log": [
                {
                    "uuid": e.uuid,
                    "published": e.published,
                    "eventType": e.event_type,
                    "actor": {"alternateId": e.actor_alternate_id, "type": e.actor_type},
                    "transaction": {"detail": {"via": e.via}},
                    "target": [
                        {"type": "AppInstance", "id": e.target_app_id},
                        {
                            "type": "User",
                            "id": e.target_user_id,
                            "alternateId": e.target_user_login,
                        },
                    ],
                }
                for e in okta.log
            ],
        }
        return cls(data, seed=world.seed if seed is None else seed, **kwargs)

    def _routes(self):
        return [
            _route("GET", r"/api/v1/users", self._users),
            _route("GET", r"/api/v1/apps", self._apps),
            _route("GET", r"/api/v1/apps/([^/]+)/users", self._app_users),
            _route("GET", r"/api/v1/logs", self._logs),
            _route("POST", r"/api/v1/users/([^/]+)/lifecycle/([a-z]+)", self._lifecycle),
            _route("POST", r"/api/v1/apps/([^/]+)/users", self._assign),
            _route("DELETE", r"/api/v1/apps/([^/]+)/users/([^/]+)", self._unassign),
        ]

    def _page(self, path: str, items: list[Any], params: Mapping[str, Any]) -> MockResponse:
        limit = self._limit(params, "limit", 200)
        try:
            start = max(0, int(params.get("after") or 0))
        except ValueError:
            return MockResponse(400, {}, {"errorCode": "E0000001", "errorSummary": "bad cursor"})
        page = items[start : start + limit]
        links = [f'<{self.base_url}{path}?{urlencode({"limit": limit})}>; rel="self"']
        if start + limit < len(items):
            query = urlencode({"after": start + limit, "limit": limit})
            links.append(f'<{self.base_url}{path}?{query}>; rel="next"')
        return MockResponse(200, {"Link": ", ".join(links)}, copy.deepcopy(page))

    def _users(self, params, body):
        return self._page("/api/v1/users", self.data["users"], params)

    def _apps(self, params, body):
        return self._page("/api/v1/apps", self.data["apps"], params)

    def _app_users(self, params, body, app_id):
        if not any(a["id"] == app_id for a in self.data["apps"]):
            return MockResponse(404, {}, {"errorCode": "E0000007", "errorSummary": "Not found"})
        items = [
            {
                "id": a["userId"],
                "scope": a["scope"],
                "status": "ACTIVE",
                "created": a["created"],
                "profile": {"role": a["profile"]["role"]},
            }
            for a in self.data["assignments"]
            if a["appId"] == app_id
        ]
        return self._page(f"/api/v1/apps/{quote(app_id)}/users", items, params)

    def _logs(self, params, body):
        return self._page("/api/v1/logs", self.data["system_log"], params)

    def _lifecycle(self, params, body, user_id, action):
        if action not in self._LIFECYCLE:
            return MockResponse(404, {}, {"errorSummary": f"unknown lifecycle action {action}"})
        for user in self.data["users"]:
            if user["id"] == user_id:
                user["status"] = self._LIFECYCLE[action]
                return MockResponse(200, {}, {})
        return MockResponse(404, {}, {"errorCode": "E0000007", "errorSummary": "Not found"})

    def _assign(self, params, body, app_id):
        """Assign a user to an app. Idempotent: a user already assigned gets a 200 and the
        existing assignment (a repeated request after a lost answer changes nothing)."""
        if not any(a["id"] == app_id for a in self.data["apps"]):
            return MockResponse(404, {}, {"errorCode": "E0000007", "errorSummary": "Not found"})
        body = body if isinstance(body, dict) else {}
        user_id = body.get("id")
        if not any(u["id"] == user_id for u in self.data["users"]):
            return MockResponse(404, {}, {"errorCode": "E0000007", "errorSummary": "Not found"})
        scope = body.get("scope", "USER")
        role = (body.get("profile") or {}).get("role", "member")
        if scope not in ("USER", "GROUP") or role not in ("member", "admin"):
            return MockResponse(400, {}, {"errorCode": "E0000001", "errorSummary": "bad body"})
        for a in self.data["assignments"]:
            if a["appId"] == app_id and a["userId"] == user_id:
                return MockResponse(200, {}, {"id": user_id, "scope": a["scope"]})
        self.data["assignments"].append(
            {
                "appId": app_id,
                "userId": user_id,
                "created": "",
                "scope": scope,
                "profile": {"role": role},
            }
        )
        return MockResponse(200, {}, {"id": user_id, "scope": scope})

    def _unassign(self, params, body, app_id, user_id):
        before = len(self.data["assignments"])
        self.data["assignments"] = [
            a
            for a in self.data["assignments"]
            if not (a["appId"] == app_id and a["userId"] == user_id)
        ]
        if len(self.data["assignments"]) == before:
            return MockResponse(404, {}, {"errorCode": "E0000007", "errorSummary": "Not found"})
        return MockResponse(204, {}, None)


# -------------------------------------------------------------------------- Google


class GoogleMock(_Mock):
    name = "google"
    base_url = GOOGLE_BASE

    @classmethod
    def from_files(cls, directory: Path, **kwargs: Any) -> "GoogleMock":
        raw = json.loads((Path(directory) / "google.json").read_text(encoding="utf-8"))
        return cls(raw, **kwargs)

    @classmethod
    def from_world(cls, world: AccessWorld, *, seed: int | None = None, **kwargs: Any):
        data = {
            "users": [
                {
                    "id": g.id,
                    "primaryEmail": g.primary_email,
                    "name": {
                        "givenName": g.given_name,
                        "familyName": g.family_name,
                        "fullName": g.full_name,
                    },
                    "suspended": g.suspended,
                    "orgUnitPath": g.org_unit_path,
                    "aliases": list(g.aliases),
                    "creationTime": g.creation_time,
                    "lastLoginTime": g.last_login_time,
                }
                for g in world.google_users
            ]
        }
        return cls(data, seed=world.seed if seed is None else seed, **kwargs)

    def _routes(self):
        return [
            _route("GET", r"/admin/directory/v1/users", self._list),
            _route("GET", r"/admin/directory/v1/users/([^/]+)", self._get),
            _route("PUT", r"/admin/directory/v1/users/([^/]+)", self._update),
            _route("PATCH", r"/admin/directory/v1/users/([^/]+)", self._update),
        ]

    def _list(self, params, body):
        limit = self._limit(params, "maxResults", 500)
        try:
            start = max(0, int(params.get("pageToken") or 0))
        except ValueError:
            return MockResponse(400, {}, {"error": {"code": 400, "message": "Invalid pageToken"}})
        users = self.data["users"]
        out: dict[str, Any] = {
            "kind": "admin#directory#users",
            "users": copy.deepcopy(users[start : start + limit]),
        }
        if start + limit < len(users):
            out["nextPageToken"] = str(start + limit)
        return MockResponse(200, {}, out)

    def _find(self, key: str) -> dict[str, Any] | None:
        for user in self.data["users"]:
            if key in (user["id"], user["primaryEmail"]) or key in user["aliases"]:
                return user
        return None

    def _get(self, params, body, key):
        user = self._find(key)
        if user is None:
            return MockResponse(404, {}, {"error": {"code": 404, "message": "Resource Not Found"}})
        return MockResponse(200, {}, copy.deepcopy(user))

    def _update(self, params, body, key):
        user = self._find(key)
        if user is None:
            return MockResponse(404, {}, {"error": {"code": 404, "message": "Resource Not Found"}})
        if not isinstance(body, dict) or not set(body) <= {"suspended", "orgUnitPath"}:
            return MockResponse(400, {}, {"error": {"code": 400, "message": "Invalid Input"}})
        user.update(body)
        return MockResponse(200, {}, copy.deepcopy(user))


# --------------------------------------------------------------------------- Slack


class SlackMock(_Mock):
    name = "slack"
    base_url = SLACK_BASE

    @classmethod
    def from_files(cls, directory: Path, **kwargs: Any) -> "SlackMock":
        raw = json.loads((Path(directory) / "slack_scim.json").read_text(encoding="utf-8"))
        return cls(raw, **kwargs)

    @classmethod
    def from_world(cls, world: AccessWorld, *, seed: int | None = None, **kwargs: Any):
        data = {
            "schemas": [SLACK_LIST],
            "totalResults": len(world.slack_users),
            "Resources": [
                {
                    "schemas": [SLACK_USER],
                    "id": s.id,
                    "userName": s.user_name,
                    "displayName": s.display_name,
                    "emails": [{"value": s.email, "primary": True}],
                    "active": s.active,
                }
                for s in world.slack_users
            ],
        }
        return cls(data, seed=world.seed if seed is None else seed, **kwargs)

    def _routes(self):
        return [
            _route("GET", r"/scim/v2/Users", self._list),
            _route("GET", r"/scim/v2/Users/([^/]+)", self._get),
            _route("PATCH", r"/scim/v2/Users/([^/]+)", self._patch),
        ]

    def _list(self, params, body):
        count = self._limit(params, "count", 1000)
        try:
            start_index = max(1, int(params.get("startIndex") or 1))
        except ValueError:
            return MockResponse(400, {}, {"Errors": {"description": "bad startIndex", "code": 400}})
        users = self.data["Resources"]
        page = users[start_index - 1 : start_index - 1 + count]
        return MockResponse(
            200,
            {},
            {
                "schemas": [SLACK_LIST],
                "totalResults": len(users),
                "startIndex": start_index,
                "itemsPerPage": len(page),
                "Resources": copy.deepcopy(page),
            },
        )

    def _find(self, user_id: str) -> dict[str, Any] | None:
        return next((u for u in self.data["Resources"] if u["id"] == user_id), None)

    def _get(self, params, body, user_id):
        user = self._find(user_id)
        if user is None:
            return MockResponse(404, {}, {"Errors": {"description": "user_not_found", "code": 404}})
        return MockResponse(200, {}, copy.deepcopy(user))

    def _patch(self, params, body, user_id):
        user = self._find(user_id)
        if user is None:
            return MockResponse(404, {}, {"Errors": {"description": "user_not_found", "code": 404}})
        operations = body.get("Operations") if isinstance(body, dict) else None
        if not isinstance(operations, list) or not operations:
            return MockResponse(400, {}, {"Errors": {"description": "invalid_patch", "code": 400}})
        for op in operations:
            if (
                not isinstance(op, dict)
                or op.get("path") != "active"
                or not isinstance(op.get("value"), bool)
                or str(op.get("op", "")).lower() not in ("replace", "add")
            ):
                return MockResponse(
                    400, {}, {"Errors": {"description": "unsupported_operation", "code": 400}}
                )
        for op in operations:
            user["active"] = op["value"]
        return MockResponse(200, {}, copy.deepcopy(user))
