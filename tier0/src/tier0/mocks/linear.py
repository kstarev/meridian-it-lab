"""In-memory mock of the slice of Linear's GraphQL API that tier0 uses."""

import copy
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tier0.clock import Clock, format_time
from tier0.tickets.linear import LinearApiError

_NAMESPACE = uuid.UUID("6f1c2a4e-5b7d-4c1a-9e3f-0a1b2c3d4e5f")

_TEAMS = (("IT", "Service Desk"), ("IDV", "Identity Verification"))
_STATES = (
    ("Triage", "triage"),
    ("Todo", "unstarted"),
    ("In Progress", "started"),
    ("Awaiting reply", "started"),
    ("Done", "completed"),
    ("Canceled", "canceled"),
)
_TIMESTAMP_FOR_TYPE = {
    "started": "startedAt",
    "completed": "completedAt",
    "canceled": "canceledAt",
}


def _uid(name: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, name))


@dataclass(frozen=True, slots=True)
class LinearMutation:
    """One logged write, including ones that failed validation."""

    name: str
    input: dict[str, Any]
    at: str


class MockLinear:
    """Mock Linear workspace with two teams, each with the same six states."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._teams: list[dict[str, Any]] = []
        for key, name in _TEAMS:
            states = [
                {"id": _uid(f"state:{key}:{state}"), "name": state, "type": kind}
                for state, kind in _STATES
            ]
            self._teams.append(
                {"id": _uid(f"team:{key}"), "key": key, "name": name, "states": {"nodes": states}}
            )
        self._issues: list[dict[str, Any]] = []
        self._counters: dict[str, int] = {}
        self._numbers: dict[str, int] = {}
        self._mutations: list[LinearMutation] = []

    @property
    def mutations(self) -> tuple[LinearMutation, ...]:
        return tuple(self._mutations)

    def reset(self) -> None:
        self._issues.clear()
        self._counters.clear()
        self._numbers.clear()
        self._mutations.clear()

    def _now(self) -> str:
        return format_time(self._clock.now())

    def _next(self, kind: str) -> int:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return self._counters[kind]

    def _log(self, name: str, payload: Mapping[str, Any]) -> None:
        self._mutations.append(LinearMutation(name, copy.deepcopy(dict(payload)), self._now()))

    def _team_by_id(self, team_id: Any) -> dict[str, Any] | None:
        return next((t for t in self._teams if t["id"] == team_id), None)

    def _find(self, ref: Any) -> dict[str, Any]:
        for issue in self._issues:
            if ref in (issue["id"], issue["identifier"]):
                return issue
        raise LinearApiError(f"Entity not found: Issue {ref!r}", "ENTITY_NOT_FOUND")

    def _issue_for_input(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ref = payload.get("issueId")
        if not ref:
            raise LinearApiError("issueId is required", "INVALID_INPUT")
        return self._find(ref)

    @staticmethod
    def _out(issue: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(issue)

    def _state_of(self, team: Mapping[str, Any], state_id: Any) -> dict[str, Any] | None:
        for state in team["states"]["nodes"]:
            if state["id"] == state_id:
                return {"id": state["id"], "name": state["name"], "type": state["type"]}
        return None

    def _enter_state(self, issue: dict[str, Any], new: dict[str, Any], now: str) -> None:
        old = issue["state"]
        issue["history"]["nodes"].append(
            {
                "id": _uid(f"history:{self._next('history')}"),
                "createdAt": now,
                "fromState": old,
                "toState": new,
            }
        )
        issue["state"] = new
        for stamp in ("completedAt", "canceledAt"):
            if _TIMESTAMP_FOR_TYPE.get(new["type"]) != stamp:
                issue[stamp] = None
        stamp = _TIMESTAMP_FOR_TYPE.get(new["type"])
        if stamp is not None and issue[stamp] is None:
            issue[stamp] = now

    # Queries

    def teams(self) -> dict[str, Any]:
        return {"nodes": copy.deepcopy(self._teams)}

    def issue(self, id: str) -> dict[str, Any]:
        return self._out(self._find(id))

    def issues(self, filter: Mapping[str, Any]) -> dict[str, Any]:
        state_name: Any = None
        team_key: Any = None
        for name, value in filter.items():
            if name == "state" and isinstance(value, Mapping) and set(value) == {"name"}:
                inner = value["name"]
                if isinstance(inner, Mapping) and set(inner) == {"eq"}:
                    state_name = inner["eq"]
                    continue
            if name == "team" and isinstance(value, Mapping) and set(value) == {"key"}:
                inner = value["key"]
                if isinstance(inner, Mapping) and set(inner) == {"eq"}:
                    team_key = inner["eq"]
                    continue
            raise LinearApiError(f"Unsupported filter {name!r} in this mock", "INVALID_INPUT")
        nodes = [
            self._out(i)
            for i in self._issues
            if (state_name is None or i["state"]["name"] == state_name)
            and (team_key is None or i["team"]["key"] == team_key)
        ]
        return {"nodes": nodes}

    # Mutations

    def issue_create(self, input: Mapping[str, Any]) -> dict[str, Any]:
        self._log("issueCreate", input)
        team = self._team_by_id(input.get("teamId"))
        if team is None:
            raise LinearApiError("teamId is missing or unknown", "INVALID_INPUT")
        if not input.get("title"):
            raise LinearApiError("title is required", "INVALID_INPUT")
        if "stateId" in input:
            state = self._state_of(team, input["stateId"])
            if state is None:
                raise LinearApiError("stateId does not belong to the team", "INVALID_INPUT")
        else:
            state = next(
                self._state_of(team, s["id"])
                for s in team["states"]["nodes"]
                if s["name"] == "Triage"
            )
        assert state is not None  # noqa: S101 - narrowing for the type checker
        now = self._now()
        key = team["key"]
        number = self._numbers.get(key, 0) + 1
        self._numbers[key] = number
        identifier = f"{key}-{number}"
        issue: dict[str, Any] = {
            "id": _uid(f"issue:{self._next('issue')}"),
            "identifier": identifier,
            "number": number,
            "title": input["title"],
            "description": input.get("description"),
            "url": f"https://linear.example/meridian/issue/{identifier}",
            "createdAt": now,
            "updatedAt": now,
            "startedAt": None,
            "completedAt": None,
            "canceledAt": None,
            "state": state,
            "team": {"id": team["id"], "key": key, "name": team["name"]},
            "comments": {"nodes": []},
            "attachments": {"nodes": []},
            "history": {"nodes": []},
        }
        issue["history"]["nodes"].append(
            {
                "id": _uid(f"history:{self._next('history')}"),
                "createdAt": now,
                "fromState": None,
                "toState": copy.deepcopy(state),
            }
        )
        stamp = _TIMESTAMP_FOR_TYPE.get(state["type"])
        if stamp is not None:
            issue[stamp] = now
        self._issues.append(issue)
        return {"success": True, "issue": self._out(issue)}

    def issue_update(self, id: str, input: Mapping[str, Any]) -> dict[str, Any]:
        self._log("issueUpdate", {"id": id, **input})
        issue = self._find(id)
        for name in input:
            if name == "teamId":
                raise LinearApiError(
                    "Moving an issue to another team is not supported in this mock",
                    "INVALID_INPUT",
                )
            if name not in ("stateId", "title", "description"):
                raise LinearApiError(f"Unsupported field {name!r} in this mock", "INVALID_INPUT")
        new_state: dict[str, Any] | None = None
        if "stateId" in input:
            team = self._team_by_id(issue["team"]["id"])
            assert team is not None  # noqa: S101 - narrowing for the type checker
            new_state = self._state_of(team, input["stateId"])
            if new_state is None:
                raise LinearApiError("stateId does not belong to the issue's team", "INVALID_INPUT")
        now = self._now()
        changed = False
        for name in ("title", "description"):
            if name in input:
                issue[name] = input[name]
                changed = True
        if new_state is not None and new_state["id"] != issue["state"]["id"]:
            self._enter_state(issue, new_state, now)
            changed = True
        if changed:
            issue["updatedAt"] = now
        return {"success": True, "issue": self._out(issue)}

    def comment_create(self, input: Mapping[str, Any]) -> dict[str, Any]:
        self._log("commentCreate", input)
        issue = self._issue_for_input(input)
        if not input.get("body"):
            raise LinearApiError("body must not be empty", "INVALID_INPUT")
        now = self._now()
        comment = {
            "id": _uid(f"comment:{self._next('comment')}"),
            "body": input["body"],
            "createdAt": now,
            "botActor": {"name": input.get("createAsUser") or "tier0", "type": "app"},
        }
        issue["comments"]["nodes"].append(comment)
        issue["updatedAt"] = now
        return {"success": True, "comment": copy.deepcopy(comment)}

    def attachment_create(self, input: Mapping[str, Any]) -> dict[str, Any]:
        self._log("attachmentCreate", input)
        issue = self._issue_for_input(input)
        if not input.get("title") or not input.get("url"):
            raise LinearApiError("title and url are required", "INVALID_INPUT")
        now = self._now()
        attachment = {
            "id": _uid(f"attachment:{self._next('attachment')}"),
            "title": input["title"],
            "url": input["url"],
            "createdAt": now,
        }
        issue["attachments"]["nodes"].append(attachment)
        issue["updatedAt"] = now
        return {"success": True, "attachment": copy.deepcopy(attachment)}
