"""ADR-004 contract suite: every TicketAdapter must pass these same tests."""

from datetime import timedelta

import pytest

from tier0.clock import MockClock, format_time
from tier0.mocks.linear import MockLinear
from tier0.tickets.adapter import (
    CANCELED,
    DONE,
    IN_PROGRESS,
    QUEUE_IDENTITY_VERIFICATION,
    QUEUE_SERVICE_DESK,
    STATES,
    TODO,
    SlackUserRef,
    TicketAdapter,
    TicketError,
)
from tier0.tickets.linear import REQUESTER_ATTACHMENT_TITLE, LinearTicketAdapter

REQUESTER = SlackUserRef(team_id="T0MERIDIAN", user_id="U0REQUEST1")


# Add "linear" once the real adapter exists; the same tests must pass against both.
@pytest.fixture(params=["mock"])
def adapter(request: pytest.FixtureRequest, clock: MockClock) -> TicketAdapter:
    return LinearTicketAdapter(MockLinear(clock))


def _open(adapter: TicketAdapter, queue: str = QUEUE_SERVICE_DESK, title: str = "Locked out"):
    return adapter.open_ticket(
        title=title, description="Cannot sign in", queue=queue, requester=REQUESTER
    )


def test_open_ticket(adapter: TicketAdapter, clock: MockClock) -> None:
    ticket = _open(adapter)
    now = format_time(clock.now())
    assert ticket.state == TODO
    assert ticket.queue == QUEUE_SERVICE_DESK
    assert ticket.requester == REQUESTER
    assert ticket.title == "Locked out"
    assert ticket.description == "Cannot sign in"
    assert ticket.comments == ()
    assert ticket.links == ()
    assert ticket.created_at == ticket.updated_at == ticket.state_changed_at == now


def test_identifiers_and_lookup(adapter: TicketAdapter) -> None:
    first = _open(adapter)
    second = _open(adapter)
    assert first.identifier != second.identifier
    assert adapter.get_ticket(first.id) == adapter.get_ticket(first.identifier)


def test_set_state_through_every_state(adapter: TicketAdapter) -> None:
    ticket = _open(adapter)
    for state in STATES:
        assert adapter.set_state(ticket.id, state).state == state


def test_state_changed_at_moves_only_on_change(adapter: TicketAdapter, clock: MockClock) -> None:
    ticket = _open(adapter)
    clock.advance(timedelta(hours=1))
    moved = adapter.set_state(ticket.id, IN_PROGRESS)
    assert moved.state_changed_at == format_time(clock.now())
    assert moved.state_changed_at != ticket.state_changed_at
    clock.advance(timedelta(hours=1))
    same = adapter.set_state(ticket.id, IN_PROGRESS)
    assert same.state_changed_at == moved.state_changed_at


def test_errors(adapter: TicketAdapter) -> None:
    ticket = _open(adapter)
    with pytest.raises(TicketError):
        adapter.set_state(ticket.id, "Bogus")
    with pytest.raises(TicketError):
        adapter.get_ticket("IT-999")
    with pytest.raises(TicketError):
        adapter.add_comment("IT-999", "hello")
    with pytest.raises(TicketError):
        adapter.open_ticket(title="t", description="d", queue="nope", requester=REQUESTER)


def test_add_comment(adapter: TicketAdapter) -> None:
    ticket = _open(adapter)
    adapter.add_comment(ticket.id, "first")
    record = adapter.add_comment(ticket.id, "second", author="alex")
    assert [(c.author, c.body) for c in record.comments] == [("tier0", "first"), ("alex", "second")]


def test_add_link_and_requester_hidden(adapter: TicketAdapter) -> None:
    ticket = _open(adapter)
    adapter.add_link(ticket.id, title="Runbook", url="https://runbook.example/reset")
    record = adapter.add_link(ticket.id, title="Log", url="https://logs.example/1")
    assert [(link.title, link.url) for link in record.links] == [
        ("Runbook", "https://runbook.example/reset"),
        ("Log", "https://logs.example/1"),
    ]
    assert all(link.url != REQUESTER.url for link in record.links)
    assert record.requester == REQUESTER


def test_requester_cannot_be_replaced(adapter: TicketAdapter) -> None:
    ticket = _open(adapter)
    with pytest.raises(TicketError, match="reserved"):
        adapter.add_link(
            ticket.id, title=REQUESTER_ATTACHMENT_TITLE, url="slack://user?team=TX&id=UX"
        )
    assert adapter.get_ticket(ticket.id).requester == REQUESTER


def test_find_tickets(adapter: TicketAdapter) -> None:
    a = _open(adapter, QUEUE_SERVICE_DESK, "a")
    b = _open(adapter, QUEUE_IDENTITY_VERIFICATION, "b")
    c = _open(adapter, QUEUE_SERVICE_DESK, "c")
    adapter.set_state(a.id, DONE)
    adapter.set_state(b.id, DONE)
    assert [t.id for t in adapter.find_tickets()] == [a.id, b.id, c.id]
    assert [t.id for t in adapter.find_tickets(state=DONE)] == [a.id, b.id]
    assert [t.id for t in adapter.find_tickets(queue=QUEUE_SERVICE_DESK)] == [a.id, c.id]
    assert [t.id for t in adapter.find_tickets(state=DONE, queue=QUEUE_SERVICE_DESK)] == [a.id]
    assert adapter.find_tickets(state=CANCELED) == ()


def test_times_follow_the_clock(adapter: TicketAdapter, clock: MockClock) -> None:
    ticket = _open(adapter)
    clock.advance(timedelta(days=3))
    record = adapter.add_comment(ticket.id, "ping")
    assert record.created_at == ticket.created_at
    assert record.updated_at == format_time(clock.now())
    assert record.updated_at != ticket.updated_at
    assert record.comments[0].created_at == format_time(clock.now())
