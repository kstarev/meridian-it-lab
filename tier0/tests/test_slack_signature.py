"""Slack request signature checks."""

from datetime import timedelta

import pytest

from meridian_dataset import World
from tier0.clock import MockClock
from tier0.identity.slack_signature import SignatureError, verify_slack_request
from tier0.mocks.slack import MockSlack


def _verify(slack: MockSlack, clock: MockClock, request) -> None:
    verify_slack_request(
        request.headers, request.body, signing_secret=slack.signing_secret, now=clock.now()
    )


def _reason(slack: MockSlack, clock: MockClock, request) -> str:
    with pytest.raises(SignatureError) as caught:
        _verify(slack, clock, request)
    return caught.value.reason


def test_valid_request_verifies(world: World, slack: MockSlack, clock: MockClock) -> None:
    ticket = world.tickets[0]
    _verify(slack, clock, slack.message_event(ticket))


def test_lowercase_header_names_verify(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    lowered = {name.lower(): value for name, value in request.headers.items()}
    verify_slack_request(
        lowered, request.body, signing_secret=slack.signing_secret, now=clock.now()
    )


def test_tampered_body(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    forged = type(request)(headers=request.headers, body=request.body + b" ")
    assert _reason(slack, clock, forged) == "bad_signature"


def test_other_secret(world: World, slack: MockSlack, clock: MockClock) -> None:
    other = MockSlack(world, clock)
    assert _reason(slack, clock, other.message_event(world.tickets[0])) == "bad_signature"


def test_stale_after_301_seconds(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    clock.advance(timedelta(seconds=301))
    assert _reason(slack, clock, request) == "stale_timestamp"


def test_exactly_300_seconds_is_ok(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    clock.advance(timedelta(seconds=300))
    _verify(slack, clock, request)


def test_future_timestamp_is_stale(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.signed_request({"a": 1}, timestamp=int(clock.now().timestamp()) + 301)
    assert _reason(slack, clock, request) == "stale_timestamp"


def test_missing_headers(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    only_ts = {"X-Slack-Request-Timestamp": request.headers["X-Slack-Request-Timestamp"]}
    for headers in ({}, only_ts):
        with pytest.raises(SignatureError) as caught:
            verify_slack_request(
                headers, request.body, signing_secret=slack.signing_secret, now=clock.now()
            )
        assert caught.value.reason == "missing_headers"


def test_bad_timestamp(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    for value in ("abc", "", "-5", " 12", "1_0"):
        headers = {**request.headers, "X-Slack-Request-Timestamp": value}
        with pytest.raises(SignatureError) as caught:
            verify_slack_request(
                headers, request.body, signing_secret=slack.signing_secret, now=clock.now()
            )
        assert caught.value.reason == "bad_timestamp"


def test_signature_without_prefix(world: World, slack: MockSlack, clock: MockClock) -> None:
    request = slack.message_event(world.tickets[0])
    bare = request.headers["X-Slack-Signature"].removeprefix("v0=")
    headers = {**request.headers, "X-Slack-Signature": bare}
    with pytest.raises(SignatureError) as caught:
        verify_slack_request(
            headers, request.body, signing_secret=slack.signing_secret, now=clock.now()
        )
    assert caught.value.reason == "bad_signature"
