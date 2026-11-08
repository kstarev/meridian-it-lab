"""The neutral ticket interface tier0 codes against (ADR-004).

Mock Linear backs it for grading and a real Linear workspace backs the demo.
One contract test suite runs against both so they cannot drift apart.
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

TRIAGE = "Triage"
TODO = "Todo"
IN_PROGRESS = "In Progress"
AWAITING_REPLY = "Awaiting reply"
DONE = "Done"
CANCELED = "Canceled"
STATES = (TRIAGE, TODO, IN_PROGRESS, AWAITING_REPLY, DONE, CANCELED)

QUEUE_SERVICE_DESK = "service-desk"
QUEUE_IDENTITY_VERIFICATION = "identity-verification"
QUEUES = (QUEUE_SERVICE_DESK, QUEUE_IDENTITY_VERIFICATION)


class TicketError(Exception):
    """Unknown ticket, queue or state, or a request the ticket system rejected."""


@dataclass(frozen=True, slots=True)
class SlackUserRef:
    """A Slack user, stored on the ticket as Slack's own deep link."""

    team_id: str
    user_id: str

    @property
    def url(self) -> str:
        return f"slack://user?team={self.team_id}&id={self.user_id}"

    @classmethod
    def from_url(cls, url: str) -> "SlackUserRef":
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        team = query.get("team", [])
        user = query.get("id", [])
        if parts.scheme != "slack" or parts.netloc != "user" or len(team) != 1 or len(user) != 1:
            raise ValueError(f"not a Slack user link: {url!r}")
        return cls(team_id=team[0], user_id=user[0])


@dataclass(frozen=True, slots=True)
class Comment:
    author: str
    body: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Link:
    title: str
    url: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TicketRecord:
    """A ticket as tier0 sees it. All times are clock.format_time strings."""

    id: str
    identifier: str
    url: str
    title: str
    description: str
    queue: str
    state: str
    requester: SlackUserRef | None
    comments: tuple[Comment, ...]
    links: tuple[Link, ...]
    created_at: str
    updated_at: str
    state_changed_at: str


class TicketAdapter(Protocol):
    """What tier0 may do with tickets.

    ticket_id is the id or the human identifier (for example "IT-1"), like
    Linear's issue(id:). An unknown ticket, queue or state raises TicketError.
    """

    def open_ticket(
        self, *, title: str, description: str, queue: str, requester: SlackUserRef
    ) -> TicketRecord: ...

    def get_ticket(self, ticket_id: str) -> TicketRecord: ...

    def add_comment(self, ticket_id: str, body: str, *, author: str = "tier0") -> TicketRecord: ...

    def set_state(self, ticket_id: str, state: str) -> TicketRecord: ...

    def add_link(self, ticket_id: str, *, title: str, url: str) -> TicketRecord: ...

    def find_tickets(
        self, *, state: str | None = None, queue: str | None = None
    ) -> tuple[TicketRecord, ...]: ...
