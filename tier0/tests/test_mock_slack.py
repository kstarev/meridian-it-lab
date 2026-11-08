import hmac
import json
from hashlib import sha256

from meridian_dataset import World
from tier0.clock import MockClock
from tier0.mocks.slack import APP_ID, BOT_USER_ID, MockSlack


def _ticket(world: World, ticket_id: str):
    return next(t for t in world.tickets if t.id == ticket_id)


def test_users_info_employee(world: World, slack: MockSlack) -> None:
    person = next(p for p in world.people if p.slack_active)
    user = slack.users_info(person.slack_user_id)["user"]
    assert user["team_id"] == world.slack_team.team_id
    assert user["profile"]["email"] == person.primary_email
    assert user["deleted"] is False


def test_users_info_external(world: World, slack: MockSlack) -> None:
    ext = world.external_slack_users[0]
    user = slack.users_info(ext.slack_user_id)["user"]
    assert user["team_id"] != world.slack_team.team_id
    assert user["profile"]["email"].endswith("@northwind.example")


def test_users_info_bot_and_unknown(slack: MockSlack) -> None:
    assert slack.users_info(BOT_USER_ID)["user"]["is_bot"] is True
    assert slack.users_info("U0NOBODY00") == {"ok": False, "error": "user_not_found"}


def test_users_info_logged(world: World, slack: MockSlack) -> None:
    slack.users_info(world.people[0].slack_user_id)
    slack.users_info("U0NOBODY00")
    assert [r.user for r in slack.reads] == [world.people[0].slack_user_id, "U0NOBODY00"]
    assert slack.reads[0].method == "users.info"


def test_chat_post_message(slack: MockSlack) -> None:
    a = slack.chat_post_message(channel="D1", text="hi")
    b = slack.chat_post_message(channel="D1", text="again", thread_ts=a["ts"])
    assert a["ok"] and b["ok"]
    assert a["message"]["app_id"] == APP_ID
    assert b["message"]["thread_ts"] == a["ts"]
    assert "thread_ts" not in a["message"]
    assert float(b["ts"]) > float(a["ts"])
    assert len({m.ts for m in slack.sent_messages}) == 2
    assert slack.sent_messages[1].thread_ts == a["ts"]
    assert slack.chat_post_message(channel="D1", text="  ") == {"ok": False, "error": "no_text"}
    assert len(slack.sent_messages) == 2


def test_message_event_envelope_and_signature(
    world: World, slack: MockSlack, clock: MockClock
) -> None:
    ticket = _ticket(world, "TKT-001")
    request = slack.message_event(ticket)
    payload = json.loads(request.body)
    team = world.slack_team.team_id
    assert payload["type"] == "event_callback"
    assert payload["team_id"] == team
    assert payload["api_app_id"] == APP_ID
    assert payload["is_ext_shared_channel"] is False
    assert payload["authorizations"][0]["user_id"] == BOT_USER_ID
    event = payload["event"]
    assert event["user"] == ticket.sender_slack_user_id
    assert event["text"] == ticket.text
    assert event["channel_type"] == "im"
    assert event["channel"] == slack.dm_channel_for(ticket.sender_slack_user_id)
    assert event["ts"] == event["event_ts"]

    stamp = request.headers["X-Slack-Request-Timestamp"]
    assert stamp == str(int(clock.now().timestamp()))
    base = b"v0:" + stamp.encode() + b":" + request.body
    expected = "v0=" + hmac.new(slack.signing_secret, base, sha256).hexdigest()
    assert hmac.compare_digest(request.headers["X-Slack-Signature"], expected)


def test_external_ticket_event(world: World, slack: MockSlack) -> None:
    ticket = _ticket(world, "TKT-006")
    payload = json.loads(slack.message_event(ticket).body)
    assert payload["event"]["user_team"] == ticket.sender_team_id
    assert ticket.sender_team_id != world.slack_team.team_id
    assert payload["is_ext_shared_channel"] is True


def test_secrets_differ_between_instances(world: World, clock: MockClock) -> None:
    assert MockSlack(world, clock).signing_secret != MockSlack(world, clock).signing_secret


def test_tampered_body_fails_signature(world: World, slack: MockSlack) -> None:
    request = slack.message_event(_ticket(world, "TKT-001"))
    stamp = int(request.headers["X-Slack-Request-Timestamp"])
    tampered = bytes([request.body[0] ^ 1]) + request.body[1:]
    assert slack.sign(request.body, stamp) == request.headers["X-Slack-Signature"]
    assert slack.sign(tampered, stamp) != request.headers["X-Slack-Signature"]


def test_reset(world: World, slack: MockSlack) -> None:
    slack.users_info(world.people[0].slack_user_id)
    first = slack.chat_post_message(channel="D1", text="hi")
    slack.reset()
    assert slack.reads == ()
    assert slack.sent_messages == ()
    assert slack.chat_post_message(channel="D1", text="hi")["ts"] == first["ts"]
