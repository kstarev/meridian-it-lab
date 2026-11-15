"""Collectors: the client's retries, read-only tokens, and every tuning account collected."""

import collections
from typing import ClassVar

import pytest

from meridian_access.collect import (
    HttpishClient,
    Mocks,
    actual_state,
    collect_google,
    collect_okta,
    collect_slack,
)
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock
from meridian_access.models import CollectorError


class Waits:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class Scripted:
    """A stand-in mock that answers from a script, then 200."""

    def __init__(self, statuses, headers=None):
        from meridian_access.mocks import MockResponse

        self._r = MockResponse
        self.statuses = list(statuses)
        self.headers = headers or {}
        self.calls = 0

    def request(self, method, path, *, token, params=None, body=None):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return self._r(status, self.headers if status == 429 else {}, {"ok": True})


def test_429_waits_for_retry_after():
    sleep, mock = Waits(), Scripted([429, 429], {"Retry-After": "3"})
    client = HttpishClient(mock, token=None, sleep=sleep)
    assert client.request("GET", "/x").status == 200
    assert sleep.waits == [3.0, 3.0] and mock.calls == 3


def test_5xx_backs_off_with_a_cap():
    sleep, mock = Waits(), Scripted([503] * 8)
    client = HttpishClient(mock, token=None, sleep=sleep, max_retries=8)
    assert client.request("GET", "/x").status == 200
    assert sleep.waits == sorted(sleep.waits)  # growing
    assert sleep.waits[0] < sleep.waits[1] and max(sleep.waits) <= 30.0
    assert sleep.waits[-1] == 30.0  # capped


def test_gives_up_after_max_retries():
    sleep, mock = Waits(), Scripted([503] * 50)
    client = HttpishClient(mock, token=None, sleep=sleep, max_retries=6)
    with pytest.raises(CollectorError) as err:
        client.request("GET", "/x")
    assert err.value.status == 503
    assert len(sleep.waits) == 6 and mock.calls == 7


def test_a_4xx_is_not_retried():
    sleep, mock = Waits(), Scripted([404])
    client = HttpishClient(mock, token=None, sleep=sleep)
    assert client.request("GET", "/x").status == 404
    assert sleep.waits == [] and mock.calls == 1
    with pytest.raises(CollectorError):
        HttpishClient(Scripted([404]), token=None, sleep=sleep).get("/x")


def test_a_collector_token_cannot_write(world):
    mocks = Mocks(
        OktaMock.from_world(world, fault_rate=0),
        GoogleMock.from_world(world, fault_rate=0),
        SlackMock.from_world(world, fault_rate=0),
    )
    user = world.okta.users[0].id
    okta = HttpishClient(mocks.okta, mocks.okta.issue_token("c", {"read"}), sleep=Waits())
    assert okta.request("POST", f"/api/v1/users/{user}/lifecycle/suspend").status == 403
    google = HttpishClient(mocks.google, mocks.google.issue_token("c", {"read"}), sleep=Waits())
    email = world.google_users[0].primary_email
    assert (
        google.request("PUT", f"/admin/directory/v1/users/{email}", body={"suspended": True}).status
        == 403
    )
    slack = HttpishClient(mocks.slack, mocks.slack.issue_token("c", {"read"}), sleep=Waits())
    body = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
    assert (
        slack.request("PATCH", f"/scim/v2/Users/{world.slack_users[0].id}", body=body).status == 403
    )
    # ...and nothing changed
    assert (
        next(u for u in mocks.okta.data["users"] if u["id"] == user)["status"]
        == world.okta.users[0].status
    )


def test_actual_state_issues_read_only_tokens(world, policy):
    mocks = Mocks(
        OktaMock.from_world(world, fault_rate=0),
        GoogleMock.from_world(world, fault_rate=0),
        SlackMock.from_world(world, fault_rate=0),
    )
    actual_state(mocks, policy)
    for mock in (mocks.okta, mocks.google, mocks.slack):
        assert mock._tokens and all(t.scopes == {"read"} for t in mock._tokens)


def test_every_tuning_account_is_collected(world, policy):
    waits = Waits()
    mocks = Mocks(
        OktaMock.from_world(world, fault_rate=0),
        GoogleMock.from_world(world, fault_rate=0),
        SlackMock.from_world(world, fault_rate=0),
    )
    client = lambda m: HttpishClient(m, m.issue_token("c"), sleep=waits)  # noqa: E731
    okta, log = collect_okta(client(mocks.okta))
    google = collect_google(client(mocks.google))
    slack = collect_slack(client(mocks.slack))
    users = [a for a in okta if not a.app_id]
    assigned = [a for a in okta if a.app_id]
    assert len(users) == len(world.okta.users)
    assert len(assigned) == len(world.okta.assignments)
    assert len(log) == len(world.okta.log)
    assert len(google) == len(world.google_users) and len(slack) == len(world.slack_users)
    assert waits.waits == []  # no faults, no waiting
    # an assignment carries the catalogue app id, not the Okta app id
    catalogue = {a.app_id for a in world.okta.apps}
    assert {a.app_id for a in assigned} <= catalogue and len(catalogue) == 45
    assert collections.Counter(a.state for a in users) == collections.Counter(
        {"ACTIVE": "active", "SUSPENDED": "suspended", "DEPROVISIONED": "deprovisioned"}[u.status]
        for u in world.okta.users
    )
    # an assignment carries its user's status: a suspended or deprovisioned user's isn't active
    status_of = {u.id: u.status for u in world.okta.users}
    want = {"ACTIVE": "active", "SUSPENDED": "suspended", "DEPROVISIONED": "deprovisioned"}
    assert collections.Counter(a.state for a in assigned) == collections.Counter(
        want[status_of[x.user_id]] for x in world.okta.assignments
    )
    assert collections.Counter(a.role for a in assigned) == collections.Counter(
        a.role for a in world.okta.assignments
    )
    assert {a.account for a in google} == {g.primary_email for g in world.google_users}
    assert {a.account for a in slack} == {s.user_name for s in world.slack_users}
    assert sum(1 for a in google if a.aliases) == sum(1 for g in world.google_users if g.aliases)
    assert all(e.app and e.user_login for e in log)


def test_actual_state_from_a_directory_has_everything_and_no_answer_key(world, world_dir, policy):
    state = actual_state(world_dir, policy)
    assert not (world_dir / "answer_key.json").exists()
    # the export rows the generator wrote: non-blank, exact duplicates collapsed
    exports = sum(
        len({tuple(c.strip() for c in row) for row in e.rows if any(c.strip() for c in row)})
        for e in world.exports
    )
    assert exports == sum(1 for a in state.accounts if a.app_id and a.system != "okta")
    expected = len(world.okta.users) + len(world.okta.assignments)
    expected += len(world.google_users) + len(world.slack_users) + exports
    assert len(state.accounts) == expected
    assert state.exports_result is not None and not state.exports_result.unreadable
    assert len(state.log) == len(world.okta.log)


def test_the_digest_is_stable_and_survives_faults(world_dir, policy):
    clean = actual_state(world_dir, policy)
    assert clean.digest() == actual_state(world_dir, policy).digest()
    waits = Waits()
    faulty = actual_state(world_dir, policy, sleep=waits, fault_rate=0.3)
    assert waits.waits  # it really was rate limited and retried
    assert faulty.digest() == clean.digest()


def test_the_digest_changes_when_an_account_changes(world, policy):
    mocks = Mocks(
        OktaMock.from_world(world, fault_rate=0),
        GoogleMock.from_world(world, fault_rate=0),
        SlackMock.from_world(world, fault_rate=0),
    )
    before = actual_state(mocks, policy).digest()
    rw = mocks.okta.issue_token("apply", {"read", "write"})
    mocks.okta.request(
        "POST", f"/api/v1/users/{world.okta.users[0].id}/lifecycle/suspend", token=rw
    )
    assert actual_state(mocks, policy).digest() != before
    mocks.okta.reset()
    assert actual_state(mocks, policy).digest() == before


# -------------------------------------------------------------------------- paging


def test_google_paging_at_a_page_size_of_7(world):
    mock = GoogleMock.from_world(world, fault_rate=0)
    client = HttpishClient(mock, mock.issue_token("c"), sleep=Waits())
    users = collect_google(client, page_size=7)
    assert len(users) == len(world.google_users)
    assert {a.account for a in users} == {g.primary_email for g in world.google_users}
    assert mock.request_count == -(-len(world.google_users) // 7)  # one request per page


class Pages:
    """A stand-in mock that serves a list of (status, headers, json) responses, then repeats
    its last one."""

    def __init__(self, *responses):
        from meridian_access.mocks import MockResponse

        self._r = MockResponse
        self.responses = list(responses)
        self.calls = 0

    def request(self, method, path, *, token, params=None, body=None):
        self.calls += 1
        status, headers, body = self.responses[min(self.calls, len(self.responses)) - 1]
        return self._r(status, headers, body)


def _link(cursor):
    return {"Link": f'<https://x.example/api/v1/logs?after={cursor}&limit=200>; rel="next"'}


def test_a_repeated_okta_cursor_stops_with_a_collector_error():
    mock = Pages(
        (200, _link("A"), [{"n": 1}]), (200, _link("B"), [{"n": 2}]), (200, _link("A"), [{"n": 3}])
    )
    client = HttpishClient(mock, token=None, sleep=Waits())
    with pytest.raises(CollectorError, match="paging loop"):
        list(client.okta_pages("/api/v1/logs"))
    assert mock.calls == 3


def test_the_page_cap_stops_a_list_that_never_ends():
    counter = iter(range(10**6))

    class Endless(Pages):
        def request(self, method, path, *, token, params=None, body=None):
            self.calls += 1
            return self._r(200, _link(f"c{next(counter)}"), [{"n": self.calls}])

    mock = Endless()
    client = HttpishClient(mock, token=None, sleep=Waits(), max_pages=25)
    with pytest.raises(CollectorError, match="more than 25 pages"):
        list(client.okta_pages("/api/v1/logs"))
    assert mock.calls == 26
    assert HttpishClient(mock, token=None).max_pages == 10_000  # the default cap


def test_the_logs_stop_when_a_page_returns_no_events():
    # the server keeps offering a next link, but the page is empty: that is the end
    mock = Pages((200, _link("A"), [{"n": 1}]), (200, _link("B"), []))
    client = HttpishClient(mock, token=None, sleep=Waits())
    assert list(client.okta_pages("/api/v1/logs")) == [{"n": 1}]
    assert mock.calls == 2


def test_google_and_slack_loops_are_stopped_too():
    page = {"users": [], "nextPageToken": "same"}
    client = HttpishClient(Pages((200, {}, page)), token=None, sleep=Waits())
    with pytest.raises(CollectorError, match="paging loop"):
        collect_google(client)
    capped = HttpishClient(
        Pages((200, {}, {"users": [], "nextPageToken": "x"})),
        token=None,
        sleep=Waits(),
        max_pages=1,
    )
    with pytest.raises(CollectorError):
        collect_google(capped)
    stuck = {"Resources": [], "totalResults": 99}  # an empty page ends Slack paging
    assert collect_slack(HttpishClient(Pages((200, {}, stuck)), token=None, sleep=Waits())) == []


def test_google_passes_an_opaque_page_token_through_unchanged():
    seen = []

    class Opaque:
        tokens: ClassVar[list[str]] = ["CjQKGRIV+/==", "Zm9v_bar-9", ""]

        def request(self, method, path, *, token, params=None, body=None):
            from meridian_access.mocks import MockResponse

            seen.append((params or {}).get("pageToken", ""))
            i = len(seen) - 1
            user = {"id": f"u{i}", "primaryEmail": f"u{i}@meridian.example"}
            return MockResponse(200, {}, {"users": [user], "nextPageToken": self.tokens[i]})

    users = collect_google(HttpishClient(Opaque(), token=None, sleep=Waits()), page_size=1)
    assert len(users) == 3 and seen == ["", "CjQKGRIV+/==", "Zm9v_bar-9"]


def test_a_suspended_or_deprovisioned_users_assignments_are_not_active(world):
    mock = OktaMock.from_world(world, fault_rate=0)
    rw = mock.issue_token("apply", {"read", "write"})
    by_user = collections.defaultdict(list)
    for a in world.okta.assignments:
        by_user[a.user_id].append(a)
    first, second = list(by_user)[:2]  # two users with assignments
    mock.request("POST", f"/api/v1/users/{first}/lifecycle/suspend", token=rw)
    mock.request("POST", f"/api/v1/users/{second}/lifecycle/deactivate", token=rw)
    client = HttpishClient(mock, mock.issue_token("c"), sleep=Waits())
    accounts, _ = collect_okta(client)
    by_ref = collections.defaultdict(set)
    for a in accounts:
        if a.app_id:
            by_ref[a.source_ref.split("@")[0]].add(a.state)
    assert by_ref[first] == {"suspended"} and by_ref[second] == {"deprovisioned"}
