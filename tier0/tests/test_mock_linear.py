"""Mock Linear specifics. Behaviour shared with real Linear lives in the contract suite."""

from datetime import timedelta

import pytest

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset import generator as dataset_generator
from tier0.clock import MockClock, format_time
from tier0.mocks.linear import MockLinear
from tier0.tickets import adapter as tier0_tickets
from tier0.tickets.linear import LinearApiError

ISSUE_KEYS = {
    "id", "identifier", "number", "title", "description", "url", "createdAt", "updatedAt",
    "startedAt", "completedAt", "canceledAt", "state", "team", "comments", "attachments",
    "history",
}  # fmt: skip


def _state_id(linear: MockLinear, key: str, name: str) -> str:
    team = next(t for t in linear.teams()["nodes"] if t["key"] == key)
    return next(s["id"] for s in team["states"]["nodes"] if s["name"] == name)


def _create(linear: MockLinear, key: str = "IT", **extra: object) -> dict:
    team = next(t for t in linear.teams()["nodes"] if t["key"] == key)
    payload = {"teamId": team["id"], "title": "T", "description": "D", **extra}
    return linear.issue_create(payload)["issue"]


def _move(linear: MockLinear, issue: dict, name: str) -> dict:
    sid = _state_id(linear, issue["team"]["key"], name)
    return linear.issue_update(issue["id"], {"stateId": sid})["issue"]


def test_teams_shape(linear: MockLinear) -> None:
    teams = linear.teams()["nodes"]
    assert [(t["key"], t["name"]) for t in teams] == [
        ("IT", "Service Desk"),
        ("IDV", "Identity Verification"),
    ]
    for team in teams:
        assert set(team) == {"id", "key", "name", "states"}
        states = team["states"]["nodes"]
        assert [s["name"] for s in states] == list(tier0_tickets.STATES)
        assert [s["type"] for s in states] == [
            "triage", "unstarted", "started", "started", "completed", "canceled",
        ]  # fmt: skip


def test_issue_shape_and_identifiers(linear: MockLinear) -> None:
    first = _create(linear)
    second = _create(linear)
    other = _create(linear, "IDV")
    assert set(first) == ISSUE_KEYS
    assert [first["identifier"], second["identifier"], other["identifier"]] == [
        "IT-1", "IT-2", "IDV-1",
    ]  # fmt: skip
    assert first["url"] == "https://linear.example/meridian/issue/IT-1"
    assert linear.issue(first["id"]) == linear.issue("IT-1")


def test_default_state_is_triage(linear: MockLinear) -> None:
    assert _create(linear)["state"]["name"] == "Triage"


def test_ids_are_deterministic(clock: MockClock) -> None:
    a, b = MockLinear(clock), MockLinear(clock)
    for linear in (a, b):
        issue = _create(linear)
        linear.comment_create({"issueId": issue["id"], "body": "x"})
        linear.attachment_create({"issueId": issue["id"], "title": "t", "url": "https://x.example"})
    assert a.teams() == b.teams()
    assert a.issue("IT-1") == b.issue("IT-1")


def test_state_timestamps(linear: MockLinear, clock: MockClock) -> None:
    issue = _create(linear)
    assert (issue["startedAt"], issue["completedAt"], issue["canceledAt"]) == (None, None, None)
    t1 = format_time(clock.now())
    issue = _move(linear, issue, "In Progress")
    assert issue["startedAt"] == t1
    clock.advance(timedelta(hours=1))
    t2 = format_time(clock.now())
    issue = _move(linear, issue, "Awaiting reply")
    assert issue["startedAt"] == t1
    issue = _move(linear, issue, "Done")
    assert issue["completedAt"] == t2
    clock.advance(timedelta(hours=1))
    issue = _move(linear, issue, "Todo")
    assert issue["completedAt"] is None
    assert issue["startedAt"] == t1
    issue = _move(linear, issue, "Canceled")
    assert issue["canceledAt"] == format_time(clock.now())
    issue = _move(linear, issue, "Todo")
    assert issue["canceledAt"] is None


def test_history_and_noop_update(linear: MockLinear, clock: MockClock) -> None:
    issue = _create(linear)
    first = issue["history"]["nodes"][0]
    assert first["fromState"] is None
    assert first["toState"]["name"] == "Triage"
    clock.advance(timedelta(hours=1))
    issue = _move(linear, issue, "Todo")
    node = issue["history"]["nodes"][-1]
    assert (node["fromState"]["name"], node["toState"]["name"]) == ("Triage", "Todo")
    before = linear.issue(issue["id"])
    clock.advance(timedelta(hours=1))
    after = _move(linear, issue, "Todo")
    assert after == before


def test_errors(linear: MockLinear) -> None:
    issue = _create(linear)
    other_state = _state_id(linear, "IDV", "Todo")

    def code(call, *args) -> str:
        with pytest.raises(LinearApiError) as info:
            call(*args)
        return info.value.code

    assert code(linear.issue, "IT-99") == "ENTITY_NOT_FOUND"
    assert code(linear.issue_update, issue["id"], {"stateId": other_state}) == "INVALID_INPUT"
    assert code(linear.issue_update, issue["id"], {"teamId": "x"}) == "INVALID_INPUT"
    assert code(linear.issues, {"assignee": {"id": {"eq": "x"}}}) == "INVALID_INPUT"
    assert code(linear.comment_create, {"issueId": issue["id"], "body": ""}) == "INVALID_INPUT"
    assert code(linear.issue_create, {"teamId": "nope", "title": "t"}) == "INVALID_INPUT"


def test_mutations_logged_including_failures(linear: MockLinear) -> None:
    issue = _create(linear)
    with pytest.raises(LinearApiError):
        linear.comment_create({"issueId": issue["id"], "body": ""})
    linear.comment_create({"issueId": issue["id"], "body": "ok"})
    assert [m.name for m in linear.mutations] == ["issueCreate", "commentCreate", "commentCreate"]
    assert linear.mutations[1].input["body"] == ""
    assert linear.mutations[0].at == issue["createdAt"]


def test_returns_copies(linear: MockLinear) -> None:
    issue = _create(linear)
    issue["title"] = "changed"
    issue["comments"]["nodes"].append({"id": "x"})
    linear.teams()["nodes"].clear()
    fresh = linear.issue("IT-1")
    assert fresh["title"] == "T"
    assert fresh["comments"]["nodes"] == []
    assert len(linear.teams()["nodes"]) == 2


def test_reset(linear: MockLinear) -> None:
    _create(linear)
    linear.reset()
    assert linear.mutations == ()
    assert linear.issues({})["nodes"] == []
    assert _create(linear)["identifier"] == "IT-1"


def test_vocabulary_matches_dataset() -> None:
    assert tier0_tickets.QUEUE_SERVICE_DESK == dataset_generator.QUEUE_SERVICE_DESK
    assert (
        tier0_tickets.QUEUE_IDENTITY_VERIFICATION == dataset_generator.QUEUE_IDENTITY_VERIFICATION
    )
    entries = generate(DEFAULT_SEED).answer_key.entries
    assert entries
    assert {e.expected_state for e in entries} <= set(tier0_tickets.STATES)
