"""Mock of the Slack Web API (users.info, chat.postMessage) and Events API."""

import copy
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from meridian_dataset import World
from meridian_dataset.model import ExternalSlackUser, Person, Ticket
from tier0.clock import Clock, format_time, parse_time

APP_ID = "A0TIER0APP1"
BOT_USER_ID = "U0TIER0BOT1"
BOT_ID = "B0TIER0BOT1"

_TIMEZONES = {"Americas": "America/New_York", "EMEA": "Europe/Berlin", "APAC": "Asia/Singapore"}
# Slack's own locale codes, derived from the person's country the way tz comes from the hub,
# so the dataset doesn't change. Slack has no Polish or Dutch UI, so those people use en-GB.
_LOCALES = {
    "US": "en-US",
    "CA": "en-US",
    "BR": "pt-BR",
    "MX": "es-LA",
    "DE": "de-DE",
    "GB": "en-GB",
    "PL": "en-GB",
    "NL": "en-GB",
    "ES": "es-ES",
    "IE": "en-GB",
    "SG": "en-GB",
    "AU": "en-GB",
    "JP": "ja-JP",
    "IN": "en-GB",
}
DEFAULT_LOCALE = "en-US"


@dataclass(frozen=True, slots=True)
class SlackHttpRequest:
    """What Slack would POST to our endpoint: headers and the raw body."""

    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class SlackRead:
    """One logged users.info or conversations.open call."""

    method: str
    user: str
    at: str
    include_locale: bool = False


# Public channels tier0 knows by name (ADR-020): #general for holiday notes, #it-help for
# the pulse check, #it-team for previews.
CHANNELS = {"general": "C0GENERAL1", "it-help": "C0ITHELP01", "it-team": "C0ITTEAM01"}


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """A message a person posted in a channel (tests put these in)."""

    channel: str
    user: str
    text: str
    ts: str


@dataclass(frozen=True, slots=True)
class SentMessage:
    """One message the bot posted."""

    channel: str
    text: str
    ts: str
    thread_ts: str | None
    blocks: tuple[dict[str, Any], ...] = ()


class MockSlack:
    """In-process Slack. The signing secret is random per run and never stored."""

    def __init__(self, world: World, clock: Clock, signing_secret: bytes | None = None) -> None:
        self._world = world
        self._clock = clock
        self._secret = signing_secret if signing_secret is not None else secrets.token_bytes(32)
        self._reads: list[SlackRead] = []
        self._sent: list[SentMessage] = []
        self._human: list[ChannelMessage] = []
        self._counter = 0

    @property
    def signing_secret(self) -> bytes:
        return self._secret

    @property
    def team_id(self) -> str:
        return self._world.slack_team.team_id

    @property
    def reads(self) -> tuple[SlackRead, ...]:
        return tuple(self._reads)

    @property
    def sent_messages(self) -> tuple[SentMessage, ...]:
        return tuple(self._sent)

    def reset(self) -> None:
        self._reads.clear()
        self._sent.clear()
        self._human.clear()
        self._counter = 0

    def _epoch(self) -> int:
        return int(self._clock.now().timestamp())

    def _next_ts(self) -> str:
        """Unique, increasing message timestamps, like Slack's."""
        self._counter += 1
        return f"{self._epoch()}.{self._counter:06d}"

    def users_info(self, user: str, *, include_locale: bool = False) -> dict[str, Any]:
        """Like users.info. Identity comes from here, not from event fields.

        As in Slack, `locale` is only included when `include_locale` is true.
        """
        self._reads.append(
            SlackRead(
                method="users.info",
                user=user,
                at=format_time(self._clock.now()),
                include_locale=include_locale,
            )
        )
        if user == BOT_USER_ID:
            return {"ok": True, "user": self._bot()}
        person = self._world.person_by_slack_id(user)
        external = next(
            (e for e in self._world.external_slack_users if e.slack_user_id == user), None
        )
        if person is not None:
            record = self._employee(person)
            locale = _LOCALES.get(person.country, DEFAULT_LOCALE)
        elif external is not None:
            record = self._external(external)
            locale = DEFAULT_LOCALE
        else:
            return {"ok": False, "error": "user_not_found"}
        if include_locale:
            record["locale"] = locale
        return {"ok": True, "user": record}

    def conversations_open(self, *, users: str) -> dict[str, Any]:
        """Like conversations.open for one user: the DM channel tier0 can post to."""
        self._reads.append(
            SlackRead(method="conversations.open", user=users, at=format_time(self._clock.now()))
        )
        known = self._world.person_by_slack_id(users) is not None or any(
            e.slack_user_id == users for e in self._world.external_slack_users
        )
        if not known:
            return {"ok": False, "error": "user_not_found"}
        return {"ok": True, "channel": {"id": self.dm_channel_for(users)}}

    def _employee(self, person: Person) -> dict[str, Any]:
        local = person.primary_email.split("@", 1)[0]
        team = self.team_id
        return {
            "id": person.slack_user_id,
            "team_id": team,
            "name": local,
            "deleted": not person.slack_active,
            "real_name": person.full_name,
            "tz": _TIMEZONES.get(person.hub, "UTC"),
            "is_admin": False,
            "is_owner": False,
            "is_bot": False,
            "is_app_user": False,
            "is_restricted": False,
            "is_ultra_restricted": False,
            "updated": int(parse_time(self._world.reference_time).timestamp()),
            "profile": {
                "real_name": person.full_name,
                "display_name": local,
                "first_name": person.given_name,
                "last_name": person.family_name,
                "title": person.title,
                "email": person.primary_email,
                "team": team,
            },
        }

    @staticmethod
    def _external(ext: ExternalSlackUser) -> dict[str, Any]:
        return {
            "id": ext.slack_user_id,
            "team_id": ext.team_id,
            "name": ext.display_name,
            "deleted": False,
            "real_name": ext.real_name,
            "is_bot": False,
            "is_restricted": False,
            "profile": {
                "real_name": ext.real_name,
                "display_name": ext.display_name,
                "email": ext.email,
                "team": ext.team_id,
            },
        }

    def _bot(self) -> dict[str, Any]:
        return {
            "id": BOT_USER_ID,
            "team_id": self.team_id,
            "name": "tier0",
            "deleted": False,
            "real_name": "tier0",
            "is_bot": True,
            "profile": {
                "real_name": "tier0",
                "bot_id": BOT_ID,
                "team": self.team_id,
            },
        }

    def chat_post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Like chat.postMessage. Records the message for grading."""
        if not text.strip():
            return {"ok": False, "error": "no_text"}
        ts = self._next_ts()
        self._sent.append(
            SentMessage(
                channel=channel,
                text=text,
                ts=ts,
                thread_ts=thread_ts,
                blocks=tuple(copy.deepcopy(blocks or [])),
            )
        )
        message: dict[str, Any] = {
            "type": "message",
            "user": BOT_USER_ID,
            "bot_id": BOT_ID,
            "app_id": APP_ID,
            "text": text,
            "ts": ts,
        }
        if thread_ts is not None:
            message["thread_ts"] = thread_ts
        return copy.deepcopy({"ok": True, "channel": channel, "ts": ts, "message": message})

    def post_as_person(self, channel: str, user: str, text: str) -> ChannelMessage:
        """A person posts in a channel, now (tests use this for #it-help activity)."""
        message = ChannelMessage(channel=channel, user=user, text=text, ts=self._next_ts())
        self._human.append(message)
        return message

    def conversations_history(self, *, channel: str, oldest: str | None = None) -> dict[str, Any]:
        """Like conversations.history: newest first, people's and the bot's messages alike,
        so callers have to tell them apart (the bot's carry bot_id)."""
        self._reads.append(
            SlackRead(
                method="conversations.history", user=channel, at=format_time(self._clock.now())
            )
        )
        floor = float(oldest) if oldest else float("-inf")
        people = [
            {"type": "message", "user": m.user, "text": m.text, "ts": m.ts}
            for m in self._human
            if m.channel == channel and float(m.ts) >= floor
        ]
        bot = [
            {"type": "message", "user": BOT_USER_ID, "bot_id": BOT_ID, "text": m.text, "ts": m.ts}
            for m in self._sent
            if m.channel == channel and float(m.ts) >= floor
        ]
        messages = sorted(people + bot, key=lambda m: float(m["ts"]), reverse=True)
        return {"ok": True, "messages": messages}

    def button_click(
        self, *, channel: str, user: str, action_id: str, value: str
    ) -> SlackHttpRequest:
        """The signed request Slack would POST when someone presses a button: a form
        body with one `payload` field holding the interaction JSON."""
        payload = {
            "type": "block_actions",
            "team": {"id": self.team_id},
            "user": {"id": user},
            "channel": {"id": channel},
            "actions": [{"type": "button", "action_id": action_id, "value": value}],
        }
        body = urlencode({"payload": json.dumps(payload, separators=(",", ":"))}).encode()
        ts = self._epoch()
        return SlackHttpRequest(
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": self.sign(body, ts),
            },
            body=body,
        )

    @staticmethod
    def dm_channel_for(slack_user_id: str) -> str:
        """A stable fake DM channel id for a user."""
        return "D" + hashlib.sha256(slack_user_id.encode()).hexdigest()[:10].upper()

    def sign(self, body: bytes, timestamp: int) -> str:
        """Slack v0 signature over 'v0:<timestamp>:<body>'."""
        base = b"v0:" + str(timestamp).encode() + b":" + body
        return "v0=" + hmac.new(self._secret, base, hashlib.sha256).hexdigest()

    def signed_request(
        self, payload: Mapping[str, Any], *, timestamp: int | None = None
    ) -> SlackHttpRequest:
        """Build a signed request. Tests use it to forge or spoof events."""
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ts = self._epoch() if timestamp is None else timestamp
        return SlackHttpRequest(
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": self.sign(body, ts),
            },
            body=body,
        )

    def message_event(self, ticket: Ticket) -> SlackHttpRequest:
        """The Events API request Slack would POST for this ticket's DM.

        The event's own team fields are NOT trusted for identity; the gateway
        asks users.info instead.
        """
        team = self.team_id
        sender = ticket.sender_slack_user_id
        ts = self._next_ts()
        payload = {
            "type": "event_callback",
            "team_id": team,
            "api_app_id": APP_ID,
            "event_id": "Ev" + hashlib.sha256(ticket.id.encode()).hexdigest()[:10].upper(),
            "event_time": self._epoch(),
            "authorizations": [
                {
                    "enterprise_id": None,
                    "team_id": team,
                    "user_id": BOT_USER_ID,
                    "is_bot": True,
                    "is_enterprise_install": False,
                }
            ],
            "is_ext_shared_channel": ticket.sender_team_id != team,
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": self.dm_channel_for(sender),
                "user": sender,
                "user_team": ticket.sender_team_id,
                "source_team": ticket.sender_team_id,
                "team": ticket.sender_team_id,
                "text": ticket.text,
                "ts": ts,
                "event_ts": ts,
            },
        }
        return self.signed_request(payload)
