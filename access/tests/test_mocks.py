"""The Okta, Google and Slack mocks: paging, faults, auth, writes and reset."""

import json
import re

import pytest

from meridian_access.mocks import GoogleMock, OktaMock, SlackMock, Token


@pytest.fixture
def okta(world):
    mock = OktaMock.from_world(world, fault_rate=0)
    return mock, mock.issue_token("r", {"read"}), mock.issue_token("rw", {"read", "write"})


def _next_after(response) -> str | None:
    m = re.search(r'[?&]after=(\d+)[^>]*>; rel="next"', response.headers.get("Link", ""))
    return m.group(1) if m else None


def test_okta_pages_follow_the_link_header(world, okta):
    mock, read, _ = okta
    seen, after, pages = [], None, 0
    while True:
        params = {"limit": 50, **({"after": after} if after else {})}
        r = mock.request("GET", "/api/v1/users", token=read, params=params)
        assert r.status == 200 and len(r.json) <= 50
        seen += r.json
        pages += 1
        after = _next_after(r)
        if not after:
            break
    assert pages > 1  # the tuning world needs several pages
    assert len(seen) == len(world.okta.users) == len({u["id"] for u in seen})


def test_google_pages_with_page_token(world):
    mock = GoogleMock.from_world(world, fault_rate=0)
    tok = mock.issue_token("r")
    users, token, pages = [], "", 0
    while True:
        params = {"pageToken": token} if token else {}
        r = mock.request("GET", "/admin/directory/v1/users", token=tok, params=params)
        users += r.json["users"]
        pages += 1
        token = r.json.get("nextPageToken", "")
        if not token:
            break
    assert pages > 1 and len(users) == len(world.google_users)


def test_slack_pages_with_start_index_and_count(world):
    mock = SlackMock.from_world(world, fault_rate=0)
    tok = mock.issue_token("r")
    first = mock.request("GET", "/scim/v2/Users", token=tok, params={"startIndex": 1, "count": 100})
    assert first.json["totalResults"] == len(world.slack_users)
    assert first.json["startIndex"] == 1 and first.json["itemsPerPage"] == 100
    second = mock.request(
        "GET", "/scim/v2/Users", token=tok, params={"startIndex": 101, "count": 100}
    )
    assert second.json["startIndex"] == 101
    assert first.json["Resources"][0]["id"] != second.json["Resources"][0]["id"]


def test_default_page_size_is_small(world):
    mock = SlackMock.from_world(world, fault_rate=0)
    tok = mock.issue_token("r")
    r = mock.request("GET", "/scim/v2/Users", token=tok)
    assert r.json["itemsPerPage"] == 50


def test_faults_are_429_with_retry_after_or_503_and_seeded(world):
    def statuses(seed):
        mock = OktaMock.from_world(world, seed=seed, fault_rate=0.5)
        tok = mock.issue_token("r")
        out = []
        for _ in range(60):
            r = mock.request("GET", "/api/v1/apps", token=tok)
            if r.status == 429:
                assert r.headers["Retry-After"] == "1"
            out.append(r.status)
        return out

    a = statuses(7)
    assert a == statuses(7)  # deterministic per seed
    assert a != statuses(8)
    assert {s for s in a} <= {200, 429, 503} and 429 in a and 503 in a


def test_default_fault_share_is_about_five_percent(world):
    mock = OktaMock.from_world(world)
    assert mock.fault_rate == 0.05
    tok = mock.issue_token("r")
    bad = sum(mock.request("GET", "/api/v1/apps", token=tok).status != 200 for _ in range(2000))
    assert 40 <= bad <= 180


def test_fault_rate_zero_never_fails(world, okta):
    mock, read, _ = okta
    assert all(mock.request("GET", "/api/v1/apps", token=read).status == 200 for _ in range(300))


def test_401_403_404(world, okta):
    mock, read, rw = okta
    assert mock.request("GET", "/api/v1/users", token=None).status == 401
    assert (
        mock.request("GET", "/api/v1/users", token=Token("nobody", frozenset({"read"}))).status
        == 401
    )
    forged = Token("r", frozenset({"read", "write"}))  # same name, different scopes
    assert mock.request("GET", "/api/v1/users", token=forged).status == 401
    user = world.okta.users[0].id
    path = f"/api/v1/users/{user}/lifecycle/suspend"
    assert mock.request("POST", path, token=read).status == 403
    assert mock.request("GET", "/api/v1/nothing", token=read).status == 404
    assert mock.request("POST", "/api/v1/users/nope/lifecycle/suspend", token=rw).status == 404
    assert mock.request("POST", f"/api/v1/users/{user}/lifecycle/explode", token=rw).status == 404


def test_okta_writes_change_state_and_reset_undoes_them(world, okta):
    mock, _, rw = okta
    user = world.okta.users[0].id
    assert mock.request("POST", f"/api/v1/users/{user}/lifecycle/suspend", token=rw).status == 200
    assert next(u for u in mock.data["users"] if u["id"] == user)["status"] == "SUSPENDED"
    mock.request("POST", f"/api/v1/users/{user}/lifecycle/unsuspend", token=rw)
    assert next(u for u in mock.data["users"] if u["id"] == user)["status"] == "ACTIVE"
    mock.request("POST", f"/api/v1/users/{user}/lifecycle/deactivate", token=rw)
    assert next(u for u in mock.data["users"] if u["id"] == user)["status"] == "DEPROVISIONED"
    a = world.okta.assignments[0]
    n = len(mock.data["assignments"])
    r = mock.request("DELETE", f"/api/v1/apps/{a.app_id}/users/{a.user_id}", token=rw)
    assert r.status == 204 and len(mock.data["assignments"]) == n - 1
    again = mock.request("DELETE", f"/api/v1/apps/{a.app_id}/users/{a.user_id}", token=rw)
    assert again.status == 404
    mock.reset()
    assert mock.data == json.loads(json.dumps(mock._initial))
    assert len(mock.data["assignments"]) == n
    assert mock.request("GET", "/api/v1/apps", token=rw).status == 200  # tokens survive


def test_google_update_suspends_and_reset(world):
    mock = GoogleMock.from_world(world, fault_rate=0)
    read, rw = mock.issue_token("r"), mock.issue_token("rw", {"read", "write"})
    email = world.google_users[0].primary_email
    path = f"/admin/directory/v1/users/{email}"
    assert mock.request("PUT", path, token=read, body={"suspended": True}).status == 403
    assert mock.request("PUT", path, token=rw, body={"suspended": True}).json["suspended"] is True
    assert mock.request("GET", path, token=read).json["suspended"] is True
    assert mock.request("PUT", path, token=rw, body={"hacked": 1}).status == 400
    assert mock.request("PUT", "/admin/directory/v1/users/none@x", token=rw, body={}).status == 404
    mock.reset()
    assert (
        mock.request("GET", path, token=read).json["suspended"] == world.google_users[0].suspended
    )


def test_slack_patch_active_and_reset(world):
    mock = SlackMock.from_world(world, fault_rate=0)
    read, rw = mock.issue_token("r"), mock.issue_token("rw", {"read", "write"})
    uid = world.slack_users[0].id
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    assert mock.request("PATCH", f"/scim/v2/Users/{uid}", token=read, body=body).status == 403
    assert (
        mock.request("PATCH", f"/scim/v2/Users/{uid}", token=rw, body=body).json["active"] is False
    )
    assert mock.request("GET", f"/scim/v2/Users/{uid}", token=read).json["active"] is False
    bad = {"Operations": [{"op": "replace", "path": "userName", "value": "x"}]}
    assert mock.request("PATCH", f"/scim/v2/Users/{uid}", token=rw, body=bad).status == 400
    mock.reset()
    assert (
        mock.request("GET", f"/scim/v2/Users/{uid}", token=read).json["active"]
        == world.slack_users[0].active
    )


def test_from_world_and_from_files_agree(world, world_dir):
    for cls, path, params in (
        (OktaMock, "/api/v1/users", {"limit": 200}),
        (GoogleMock, "/admin/directory/v1/users", {}),
        (SlackMock, "/scim/v2/Users", {}),
    ):
        a, b = (
            cls.from_world(world, fault_rate=0),
            cls.from_files(world_dir, seed=world.seed, fault_rate=0),
        )
        ra = a.request("GET", path, token=a.issue_token("r"), params=params)
        rb = b.request("GET", path, token=b.issue_token("r"), params=params)
        assert ra.json == rb.json
