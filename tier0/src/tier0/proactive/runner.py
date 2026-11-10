"""Sends what the scheduler says is due, with every safety limit (ADR-020).

- Text only from code templates; this module has no model at all.
- Dry-run by default: it records what it would send and posts nothing, previews included.
- A preview goes to #it-team in IT's working hours before each post, with the full post
  text and a Cancel button. What IT previewed is exactly what gets posted, or nothing is:
  if the post changed since (different text, hour or offices), it isn't sent.
- At most one post per Berlin day, checked here again after the scheduler.
- Paused on every start: nothing is kept between runs, so the runner comes up with the
  kill switch on until someone in #it-team presses Resume. It reads its own last pulse
  check back from #it-help, so a restart can't repeat one.
- The kill switch stops previews and posts. Every toggle and every refused button press
  is logged with who did it.
- Every message goes through the output guard; a blocked post isn't sent, and #it-team is
  told.
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
from tier0.proactive.calendar import COUNTRY_TZ
from tier0.proactive.schedule import CHANNEL_IT_HELP, CHANNEL_IT_TEAM, Config, Post, State, plan

PROACTIVE_PRINCIPAL = "tier0-proactive"
CANCEL_ACTION = "cancel_post"
RESUME_ACTION = "resume_posts"
# Slack message subtypes that aren't a person saying something. Human subtypes such as
# file_share, thread_broadcast or me_message do count as activity.
SYSTEM_SUBTYPES = frozenset(
    {
        "bot_message",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "pinned_item",
        "unpinned_item",
    }
)
# Slack's limit for one section block's text.
_SECTION_LIMIT = 3000


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


def _is_pulse_text(text: str) -> bool:
    return any(text.startswith(v) for options in templates.PULSE.values() for v in options)


def _ts_time(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


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
        self.log: list[LogEntry] = []
        self._previews: dict[str, Post] = {}
        self._skips_logged: set[str] = set()
        self._unknown_logged: set[str] = set()
        self._pause_announced = False
        # Nothing survives a restart, so start paused and remember the last pulse from
        # #it-help itself.
        self.kill_switch = True
        self.state = State(last_pulse_at=self._last_own_pulse())
        self._note("paused_on_start", detail="kill switch on until #it-team resumes")

    # --- controls ------------------------------------------------------------------

    def set_kill_switch(self, on: bool, *, by: str) -> None:
        self.kill_switch = on
        self._note("kill_switch_on" if on else "kill_switch_off", detail=by)

    def set_incident(self, on: bool, *, by: str) -> None:
        self.state = replace(self.state, incident=on)
        self._note("incident_on" if on else "incident_off", detail=by)

    # --- inputs --------------------------------------------------------------------

    def countries(self) -> tuple[str, ...]:
        """Office countries from the Directory. A code tier0 has no time zone for is
        dropped and logged once, so it can't stop the posts for everyone else."""
        found = self._directory.resources_buildings_list(principal=PROACTIVE_PRINCIPAL)
        codes = {
            b.get("address", {}).get("regionCode")
            for b in found.get("buildings", [])
            if isinstance(b, dict)
        }
        known: set[str] = set()
        for code in codes:
            if not isinstance(code, str):
                continue
            if code in COUNTRY_TZ:
                known.add(code)
            elif code not in self._unknown_logged:
                self._unknown_logged.add(code)
                self._note("unknown_country", detail=code)
        return tuple(sorted(known))

    def _history(self) -> list[dict[str, Any]]:
        found = self._slack.conversations_history(channel=CHANNELS[CHANNEL_IT_HELP])
        return [m for m in found.get("messages", []) if isinstance(m, dict) and "ts" in m]

    def last_human_message(self) -> datetime | None:
        """The newest #it-help message from a person. tier0's own messages and system
        messages (joins, topic changes) never count; a shared file or a thread reply sent
        to the channel does."""
        for message in self._history():
            if message.get("bot_id") or message.get("subtype") in SYSTEM_SUBTYPES:
                continue
            return _ts_time(message["ts"])
        return None

    def _last_own_pulse(self) -> datetime | None:
        for message in self._history():
            if message.get("bot_id") and _is_pulse_text(str(message.get("text", ""))):
                return _ts_time(message["ts"])
        return None

    # --- the tick ------------------------------------------------------------------

    def tick(self) -> list[LogEntry]:
        """Send whatever is due now. Returns the log entries this tick added."""
        start = len(self.log)
        if self.kill_switch:
            self._announce_pause()
            return self.log[start:]
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
        for post, reason in due.skipped:
            if post.post_id not in self._skips_logged:
                self._skips_logged.add(post.post_id)
                self._note("skipped", post, post.channel, detail=reason)
        return self.log[start:]

    def _announce_pause(self) -> None:
        if self._pause_announced:
            return
        self._pause_announced = True
        text = templates.PAUSED_ON_START
        if self.dry_run or not self._guard_ok(text):
            self._note(
                "would_announce_pause" if self.dry_run else "blocked", channel=CHANNEL_IT_TEAM
            )
            return
        self._slack.chat_post_message(
            channel=CHANNELS[CHANNEL_IT_TEAM],
            text=text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": RESUME_ACTION,
                            "value": "resume",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": "Resume"},
                        }
                    ],
                },
            ],
        )
        self._note("announced_pause", channel=CHANNEL_IT_TEAM)

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
        # What IT previewed is exactly what gets posted, or nothing is.
        seen = self._previews.get(post.post_id)
        if seen != post:
            self._note(
                "changed_since_preview" if seen is not None else "not_previewed",
                post,
                post.channel,
                detail="not sent: it differs from what #it-team saw" if seen else None,
            )
            self.state = replace(self.state, cancelled=self.state.cancelled | {post.post_id})
            return
        if not self._guard_ok(post.text):
            self._blocked(post)
            return
        if self.dry_run:
            self._note("would_post", post, post.channel)
        else:
            response = self._slack.chat_post_message(channel=CHANNELS[post.channel], text=seen.text)
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

    # --- buttons: Cancel and Resume --------------------------------------------------

    def handle_interaction(self, request: SlackHttpRequest) -> str:
        """A button press from Slack. Only signed presses from #it-team count; every
        refused one is logged."""
        try:
            verify_slack_request(
                request.headers, request.body, signing_secret=self._secret, now=self._clock.now()
            )
        except SignatureError as error:
            self._note("press_rejected", detail=f"signature: {error.reason}")
            return f"rejected: {error.reason}"
        try:
            payload = json.loads(parse_qs(request.body.decode())["payload"][0])
            channel = (payload.get("channel") or {}).get("id")
            user = (payload.get("user") or {}).get("id")
            actions = payload.get("actions") or []
            action = actions[0] if actions and isinstance(actions[0], dict) else {}
        except (KeyError, IndexError, ValueError, AttributeError):
            self._note("press_rejected", detail="malformed")
            return "rejected: malformed"
        action_id = action.get("action_id")
        if action_id not in (CANCEL_ACTION, RESUME_ACTION):
            self._note("press_rejected", detail=f"unknown action from {user}")
            return "ignored"
        if channel != CHANNELS[CHANNEL_IT_TEAM]:
            self._note("press_refused", detail=f"{action_id} from {channel} by {user}")
            return "refused: only works from #it-team"
        if action_id == RESUME_ACTION:
            self.set_kill_switch(False, by=str(user))
            return "resumed"
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
        """The header, the full post text (Slack shows blocks, not `text`, when both are
        sent), then the Cancel button."""
        body = [post.text[i : i + _SECTION_LIMIT] for i in range(0, len(post.text), _SECTION_LIMIT)]
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": self._preview_text(post)}},
            *({"type": "section", "text": {"type": "plain_text", "text": part}} for part in body),
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
