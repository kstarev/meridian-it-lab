"""Sends what the scheduler says is due, with every safety limit (ADR-020).

- Text only from code templates; this module has no model at all.
- Dry-run by default: it records what it would send and posts nothing, previews included.
- A preview goes to #it-team 24 h before each post, with a Cancel button. A signed button
  press from #it-team cancels the post; a press from anywhere else is refused.
- At most one post per Berlin day, checked here again after the scheduler.
- A kill switch stops previews and posts. Every toggle is logged with who did it.
- Every preview and post goes through the output guard; a blocked post isn't sent, and
  #it-team is told.
"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs

from tier0.clock import Clock
from tier0.guard import check_reply
from tier0.identity.slack_signature import SignatureError, verify_slack_request
from tier0.mocks.slack import CHANNELS, SlackHttpRequest
from tier0.proactive import templates
from tier0.proactive.schedule import CHANNEL_IT_HELP, CHANNEL_IT_TEAM, Config, Post, State, plan

PROACTIVE_PRINCIPAL = "tier0-proactive"
CANCEL_ACTION = "cancel_post"


class SlackChannelsApi(Protocol):
    def chat_post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    def conversations_history(
        self, *, channel: str, oldest: str | None = None
    ) -> dict[str, Any]: ...


class BuildingsApi(Protocol):
    def resources_buildings_list(self, *, principal: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class LogEntry:
    at: datetime
    action: str  # previewed, posted, would_preview, would_post, cancelled, blocked, ...
    post_id: str | None = None
    channel: str | None = None
    detail: str | None = None


class ProactiveRunner:
    def __init__(
        self,
        *,
        slack: SlackChannelsApi,
        directory: BuildingsApi,
        clock: Clock,
        signing_secret: bytes,
        domain: str,
        config: Config | None = None,
        dry_run: bool = True,
    ) -> None:
        self._slack = slack
        self._directory = directory
        self._clock = clock
        self._secret = signing_secret
        self._domain = domain
        self.config = config if config is not None else Config()
        self.dry_run = dry_run
        self.kill_switch = False
        self.state = State()
        self.log: list[LogEntry] = []
        self._previews: dict[str, Post] = {}

    # --- controls ------------------------------------------------------------------

    def set_kill_switch(self, on: bool, *, by: str) -> None:
        self.kill_switch = on
        self._note("kill_switch_on" if on else "kill_switch_off", detail=by)

    def set_incident(self, on: bool, *, by: str) -> None:
        self.state = replace(self.state, incident=on)
        self._note("incident_on" if on else "incident_off", detail=by)

    # --- inputs --------------------------------------------------------------------

    def countries(self) -> tuple[str, ...]:
        found = self._directory.resources_buildings_list(principal=PROACTIVE_PRINCIPAL)
        codes = {
            b.get("address", {}).get("regionCode")
            for b in found.get("buildings", [])
            if isinstance(b, dict)
        }
        return tuple(sorted(c for c in codes if isinstance(c, str)))

    def last_human_message(self) -> datetime | None:
        """The newest #it-help message from a person. tier0's own never count."""
        history = self._slack.conversations_history(channel=CHANNELS[CHANNEL_IT_HELP])
        for message in history.get("messages", []):
            if message.get("bot_id") or message.get("subtype"):
                continue
            return datetime.fromtimestamp(float(message["ts"]), tz=UTC)
        return None

    # --- the tick ------------------------------------------------------------------

    def tick(self) -> list[LogEntry]:
        """Send whatever is due now. Returns the log entries this tick added."""
        start = len(self.log)
        if self.kill_switch:
            return []
        due = plan(
            self._clock.now(),
            self.countries(),
            self.last_human_message(),
            self.state,
            self.config,
        )
        for post in due.previews:
            self._preview(post)
        for post in due.posts:
            self._post(post)
        return self.log[start:]

    def _preview(self, post: Post) -> None:
        if not self._guard_ok(post.text) or not self._guard_ok(self._preview_text(post)):
            self._blocked(post)
            return
        if self.dry_run:
            self._note("would_preview", post, CHANNEL_IT_TEAM)
        else:
            response = self._slack.chat_post_message(
                channel=CHANNELS[CHANNEL_IT_TEAM],
                text=f"{self._preview_text(post)}\n\n{post.text}",
                blocks=self._preview_blocks(post),
            )
            if not response.get("ok"):
                self._note("preview_failed", post, CHANNEL_IT_TEAM)
                return
            self._note("previewed", post, CHANNEL_IT_TEAM)
        self._previews[post.post_id] = post
        previewed = dict(self.state.previewed)
        previewed[post.post_id] = self._clock.now()
        self.state = replace(self.state, previewed=previewed)

    def _post(self, post: Post) -> None:
        if post.berlin_day in self.state.broadcast_days:
            self._note("refused_second_post_today", post, post.channel)
            return
        if not self._guard_ok(post.text):
            self._blocked(post)
            return
        if self.dry_run:
            self._note("would_post", post, post.channel)
        else:
            response = self._slack.chat_post_message(channel=CHANNELS[post.channel], text=post.text)
            if not response.get("ok"):
                self._note("post_failed", post, post.channel)
                return
            self._note("posted", post, post.channel)
        self.state = replace(
            self.state,
            sent=self.state.sent | {post.post_id},
            broadcast_days=self.state.broadcast_days | {post.berlin_day},
            last_pulse_at=self._clock.now() if post.kind == "pulse" else self.state.last_pulse_at,
        )

    # --- the Cancel button ---------------------------------------------------------

    def handle_interaction(self, request: SlackHttpRequest) -> str:
        """A button press from Slack. Only a signed Cancel from #it-team counts."""
        try:
            verify_slack_request(
                request.headers, request.body, signing_secret=self._secret, now=self._clock.now()
            )
        except SignatureError as error:
            return f"rejected: {error.reason}"
        try:
            payload = json.loads(parse_qs(request.body.decode())["payload"][0])
        except (KeyError, IndexError, ValueError):
            return "rejected: malformed"
        channel = (payload.get("channel") or {}).get("id")
        user = (payload.get("user") or {}).get("id")
        actions = payload.get("actions") or []
        action = actions[0] if actions and isinstance(actions[0], dict) else {}
        if action.get("action_id") != CANCEL_ACTION:
            return "ignored"
        if channel != CHANNELS[CHANNEL_IT_TEAM]:
            self._note("cancel_refused", detail=f"from {channel} by {user}")
            return "refused: cancel only works from #it-team"
        post_id = str(action.get("value"))
        if post_id in self.state.sent:
            return "too late: already posted"
        self.state = replace(self.state, cancelled=self.state.cancelled | {post_id})
        self._note("cancelled", post_id=post_id, channel=CHANNEL_IT_TEAM, detail=str(user))
        return "cancelled"

    # --- helpers -------------------------------------------------------------------

    def _guard_ok(self, text: str) -> bool:
        return check_reply(text, requester_email=None, domain=self._domain).allowed

    def _blocked(self, post: Post) -> None:
        self._note("blocked", post, post.channel, detail="output guard")
        self.state = replace(self.state, cancelled=self.state.cancelled | {post.post_id})
        alert = templates.BLOCKED_ALERT.format(post_id=post.post_id)
        if not self.dry_run and self._guard_ok(alert):
            self._slack.chat_post_message(channel=CHANNELS[CHANNEL_IT_TEAM], text=alert)

    def _preview_text(self, post: Post) -> str:
        when = post.send_at.strftime("%a %d %b %Y, %H:%M UTC")
        if post.kind == "pulse":
            return templates.PULSE_PREVIEW.format(when=when)
        return templates.PREVIEW.format(when=when, channel=f"#{post.channel}")

    def _preview_blocks(self, post: Post) -> list[dict[str, Any]]:
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": self._preview_text(post)}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": CANCEL_ACTION,
                        "value": post.post_id,
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Cancel"},
                    }
                ],
            },
        ]

    def _note(
        self,
        action: str,
        post: Post | None = None,
        channel: str | None = None,
        *,
        post_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.log.append(
            LogEntry(
                at=self._clock.now(),
                action=action,
                post_id=post.post_id if post is not None else post_id,
                channel=channel,
                detail=detail,
            )
        )
