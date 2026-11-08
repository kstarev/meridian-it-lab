"""TicketAdapter on top of Linear's GraphQL API (real or mock, ADR-004)."""

from collections.abc import Mapping
from typing import Any, Protocol

from tier0.tickets.adapter import (
    QUEUE_IDENTITY_VERIFICATION,
    QUEUE_SERVICE_DESK,
    TODO,
    Comment,
    Link,
    SlackUserRef,
    TicketError,
    TicketRecord,
)

# A queue is a Linear team.
QUEUE_TEAM_KEYS = {QUEUE_SERVICE_DESK: "IT", QUEUE_IDENTITY_VERIFICATION: "IDV"}
_TEAM_KEY_QUEUES = {key: queue for queue, key in QUEUE_TEAM_KEYS.items()}

# Linear has no requester field for a Slack user, so it rides along as an attachment.
REQUESTER_ATTACHMENT_TITLE = "Requester (Slack)"


class LinearApiError(Exception):
    """A Linear API error, with its extensions code such as ENTITY_NOT_FOUND."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(f"{code}: {message}")
        self.message = message
        self.code = code


class LinearApi(Protocol):
    """The subset of Linear's GraphQL API we use, as dicts in Linear's shapes."""

    def teams(self) -> dict[str, Any]: ...

    def issue(self, id: str) -> dict[str, Any]: ...

    def issues(self, filter: Mapping[str, Any]) -> dict[str, Any]: ...

    def issue_create(self, input: Mapping[str, Any]) -> dict[str, Any]: ...

    def issue_update(self, id: str, input: Mapping[str, Any]) -> dict[str, Any]: ...

    def comment_create(self, input: Mapping[str, Any]) -> dict[str, Any]: ...

    def attachment_create(self, input: Mapping[str, Any]) -> dict[str, Any]: ...


class LinearTicketAdapter:
    """Maps tier0's ticket calls onto Linear issues, comments and attachments.

    The requester is fixed when the ticket is opened: add_link refuses the
    requester title, so no later call can swap whose ticket it is.
    """

    def __init__(self, api: LinearApi) -> None:
        self._api = api
        self._teams: dict[str, dict[str, Any]] | None = None

    def _team(self, key: str) -> dict[str, Any]:
        if self._teams is None:
            try:
                nodes = self._api.teams()["nodes"]
            except LinearApiError as exc:
                raise TicketError(str(exc)) from exc
            self._teams = {team["key"]: team for team in nodes}
        if key not in self._teams:
            raise TicketError(f"no Linear team with key {key!r}")
        return self._teams[key]

    def _state_id(self, team_key: str, state: str) -> str:
        for node in self._team(team_key)["states"]["nodes"]:
            if node["name"] == state:
                return node["id"]
        raise TicketError(f"unknown state {state!r} for team {team_key}")

    def open_ticket(
        self, *, title: str, description: str, queue: str, requester: SlackUserRef
    ) -> TicketRecord:
        if queue not in QUEUE_TEAM_KEYS:
            raise TicketError(f"unknown queue {queue!r}")
        key = QUEUE_TEAM_KEYS[queue]
        team = self._team(key)
        try:
            created = self._api.issue_create(
                {
                    "teamId": team["id"],
                    "title": title,
                    "description": description,
                    "stateId": self._state_id(key, TODO),
                }
            )
            issue_id = created["issue"]["id"]
            self._api.attachment_create(
                {"issueId": issue_id, "title": REQUESTER_ATTACHMENT_TITLE, "url": requester.url}
            )
            return self._record(self._api.issue(issue_id))
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc

    def get_ticket(self, ticket_id: str) -> TicketRecord:
        try:
            return self._record(self._api.issue(ticket_id))
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc

    def add_comment(self, ticket_id: str, body: str, *, author: str = "tier0") -> TicketRecord:
        try:
            issue = self._api.issue(ticket_id)
            self._api.comment_create({"issueId": issue["id"], "body": body, "createAsUser": author})
            return self._record(self._api.issue(issue["id"]))
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc

    def set_state(self, ticket_id: str, state: str) -> TicketRecord:
        try:
            issue = self._api.issue(ticket_id)
            state_id = self._state_id(issue["team"]["key"], state)
            self._api.issue_update(issue["id"], {"stateId": state_id})
            return self._record(self._api.issue(issue["id"]))
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc

    def add_link(self, ticket_id: str, *, title: str, url: str) -> TicketRecord:
        if title == REQUESTER_ATTACHMENT_TITLE:
            raise TicketError(f"{title!r} is reserved: the requester is set only when opening")
        try:
            issue = self._api.issue(ticket_id)
            self._api.attachment_create({"issueId": issue["id"], "title": title, "url": url})
            return self._record(self._api.issue(issue["id"]))
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc

    def find_tickets(
        self, *, state: str | None = None, queue: str | None = None
    ) -> tuple[TicketRecord, ...]:
        flt: dict[str, Any] = {}
        if state is not None:
            flt["state"] = {"name": {"eq": state}}
        if queue is not None:
            if queue not in QUEUE_TEAM_KEYS:
                raise TicketError(f"unknown queue {queue!r}")
            flt["team"] = {"key": {"eq": QUEUE_TEAM_KEYS[queue]}}
        try:
            nodes = self._api.issues(flt)["nodes"]
        except LinearApiError as exc:
            raise TicketError(str(exc)) from exc
        return tuple(self._record(node) for node in nodes)

    @staticmethod
    def _record(issue: Mapping[str, Any]) -> TicketRecord:
        requester: SlackUserRef | None = None
        links: list[Link] = []
        for attachment in issue["attachments"]["nodes"]:
            if attachment["title"] == REQUESTER_ATTACHMENT_TITLE:
                # The first one, written by open_ticket, wins.
                if requester is None:
                    requester = SlackUserRef.from_url(attachment["url"])
            else:
                links.append(Link(attachment["title"], attachment["url"], attachment["createdAt"]))
        comments = tuple(
            Comment(c["botActor"]["name"], c["body"], c["createdAt"])
            for c in issue["comments"]["nodes"]
        )
        changed = issue["createdAt"]
        for node in issue["history"]["nodes"]:
            if node.get("toState"):
                changed = node["createdAt"]
        return TicketRecord(
            id=issue["id"],
            identifier=issue["identifier"],
            url=issue["url"],
            title=issue["title"],
            description=issue["description"],
            queue=_TEAM_KEY_QUEUES[issue["team"]["key"]],
            state=issue["state"]["name"],
            requester=requester,
            comments=comments,
            links=tuple(links),
            created_at=issue["createdAt"],
            updated_at=issue["updatedAt"],
            state_changed_at=changed,
        )
