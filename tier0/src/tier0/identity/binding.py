"""Turn an authenticated Slack event into a directory identity, or refuse."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tier0.directory_api import DirectoryApi, DirectoryError

GATEWAY_PRINCIPAL = "tier0-gateway"
# Only what the checks need (GDPR data minimisation).
DIRECTORY_FIELDS = ("id", "primaryEmail", "suspended", "archived", "name")


@dataclass(frozen=True, slots=True)
class SlackProfile:
    """What a reply needs from Slack's users.info (with include_locale)."""

    email: str | None
    locale: str | None
    tz: str | None


def slack_profile(user: Mapping[str, Any]) -> SlackProfile:
    """Read email, locale and time zone from a users.info `user` object. The one place
    this is parsed, for intake and the sweeper alike (ADR-028)."""

    def text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    profile = user.get("profile")
    return SlackProfile(
        email=text(profile.get("email")) if isinstance(profile, Mapping) else None,
        locale=text(user.get("locale")),
        tz=text(user.get("tz")),
    )


@dataclass(frozen=True, slots=True)
class ReplyTarget:
    """Who a later message (the sweeper's note) goes to, read fresh from Slack and the
    Directory. The first name is always the Directory's givenName, as at intake."""

    email: str | None
    locale: str | None
    tz: str | None
    first_name: str | None


class SlackUsersApi(Protocol):
    """The one Slack call the binder needs."""

    def users_info(self, user: str, *, include_locale: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class BoundIdentity:
    """A verified employee: the only thing a token may ever be minted for."""

    directory_id: str
    email: str
    slack_user_id: str
    slack_team_id: str
    full_name: str | None = None
    # From the directory (authoritative), for greetings only (ADR-028).
    first_name: str | None = None
    # Slack's locale from users.info with include_locale, for the reply language only.
    locale: str | None = None
    # Slack's IANA time zone (users.info "tz"), for the Friday closer only.
    tz: str | None = None


@dataclass(frozen=True, slots=True)
class Unbound:
    """No identity. `reason` says which check failed."""

    reason: str
    slack_user_id: str | None
    slack_team_id: str | None
    # Slack's locale, when users.info answered: an outsider is still replied to in their
    # language (ADR-028). It never affects any check.
    locale: str | None = None


class IdentityBinder:
    """Bind a Slack message event to one active Meridian employee.

    An outsider, a bot, a guest, a leaver or anyone not in the directory never
    gets an identity, so no token is ever minted for them. No Directory read
    happens before the Slack checks pass. The message text is never read.
    """

    def __init__(
        self,
        *,
        slack: SlackUsersApi,
        directory: DirectoryApi,
        team_id: str,
        app_id: str,
        domain: str,
    ) -> None:
        self._slack = slack
        self._directory = directory
        self._team_id = team_id
        self._app_id = app_id
        self._suffix = "@" + domain.lower()

    def bind(self, payload: Mapping[str, Any]) -> BoundIdentity | Unbound:
        raw_team = payload.get("team_id")
        envelope_team = raw_team if isinstance(raw_team, str) else None
        event = payload.get("event")
        raw_user = event.get("user") if isinstance(event, dict) else None
        user_id = raw_user if isinstance(raw_user, str) and raw_user else None

        locale: str | None = None

        def refuse(reason: str, team: str | None = envelope_team) -> Unbound:
            return Unbound(reason=reason, slack_user_id=user_id, slack_team_id=team, locale=locale)

        if (
            payload.get("type") != "event_callback"
            or payload.get("api_app_id") != self._app_id
            or payload.get("team_id") != self._team_id
        ):
            return refuse("wrong_installation")

        if (
            not isinstance(event, dict)
            or event.get("type") != "message"
            or "subtype" in event
            or "bot_id" in event
            or user_id is None
        ):
            return refuse("not_a_user_message")

        info = self._slack.users_info(user_id, include_locale=True)
        if not info.get("ok"):
            return refuse("unknown_slack_user")
        user = info.get("user")
        if not isinstance(user, dict):
            return refuse("unknown_slack_user")
        slack = slack_profile(user)
        locale, tz = slack.locale, slack.tz

        # users.info is authoritative; event["user_team"] and friends are ignored.
        info_team = user.get("team_id")
        known_team = info_team if isinstance(info_team, str) else None
        if user.get("is_bot") or user.get("is_app_user"):
            return refuse("bot_user", known_team)
        if user.get("deleted"):
            return refuse("deactivated_slack_user", known_team)
        if known_team != self._team_id:
            return refuse("external_user", known_team)
        if user.get("is_restricted") or user.get("is_ultra_restricted"):
            return refuse("guest_user", known_team)

        email = slack.email
        if email is None or not email.lower().endswith(self._suffix):
            return refuse("no_company_email", known_team)

        try:
            resource = self._directory.users_get(
                email, principal=GATEWAY_PRINCIPAL, fields=DIRECTORY_FIELDS
            )
        except DirectoryError as error:
            return refuse(
                "not_in_directory" if error.code == 404 else "directory_error", known_team
            )

        primary = resource.get("primaryEmail")
        directory_id = resource.get("id")
        if not isinstance(primary, str) or not isinstance(directory_id, str) or not directory_id:
            return refuse("directory_error", known_team)
        if primary.lower() != email.lower():
            return refuse("email_mismatch", known_team)
        if resource.get("suspended") or resource.get("archived"):
            return refuse("suspended", known_team)

        name = resource.get("name")
        full_name = name.get("fullName") if isinstance(name, dict) else None
        given_name = _given_name(resource)
        return BoundIdentity(
            directory_id=directory_id,
            email=primary,
            slack_user_id=user_id,
            slack_team_id=known_team,
            full_name=full_name if isinstance(full_name, str) and full_name else None,
            first_name=given_name,
            locale=locale,
            tz=tz,
        )

    def reply_target(self, slack_user_id: str) -> ReplyTarget | None:
        """Fresh details for a later message to an already-served requester, or None if
        Slack no longer knows them. Reads users.info once and, for the first name, the
        requester's own Directory record."""
        info = self._slack.users_info(slack_user_id, include_locale=True)
        user = info.get("user") if info.get("ok") else None
        if not isinstance(user, dict) or user.get("deleted"):
            return None
        slack = slack_profile(user)
        first_name = None
        if slack.email is not None and slack.email.lower().endswith(self._suffix):
            try:
                resource = self._directory.users_get(
                    slack.email, principal=GATEWAY_PRINCIPAL, fields=DIRECTORY_FIELDS
                )
            except DirectoryError:
                resource = {}
            first_name = _given_name(resource)
        return ReplyTarget(
            email=slack.email, locale=slack.locale, tz=slack.tz, first_name=first_name
        )


def _given_name(resource: Mapping[str, Any]) -> str | None:
    name = resource.get("name")
    given = name.get("givenName") if isinstance(name, Mapping) else None
    return given if isinstance(given, str) and given else None
