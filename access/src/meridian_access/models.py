"""Records the collectors produce: one normalised Account per account, plus log events."""

from dataclasses import dataclass, field
from typing import Literal

AccountState = Literal["active", "suspended", "deprovisioned", ""]  # "": unknown label


class CollectorError(RuntimeError):
    """A collector could not read a system (it gave up after its retries, or was refused)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Account:
    """One account in one system, in the same shape whichever system it came from.

    `system` is okta, google, slack, or the app id for a row of a CSV export. An Okta app
    assignment is one Account per (user, app): system "okta", `app_id` the catalog id.
    `account` is the login, primary email or userName; for an export row it is the cell as
    written (whitespace stripped). `state` is active, suspended or deprovisioned, or empty
    when an export's status label is unknown or blank (Slack's
    `active: false` is a deactivated user, so deprovisioned). Dates are as the source gave
    them (API timestamps) or ISO `YYYY-MM-DD` (CSV exports). `aliases` are extra addresses
    (Google).
    """

    system: str
    account: str
    email: str = ""
    given: str = ""
    family: str = ""
    display: str = ""
    state: AccountState = "active"
    app_id: str = ""
    role: str = ""  # member | admin | ""
    scope: str = ""  # GROUP | USER | ""
    created: str = ""
    last_login: str = ""
    source_ref: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.system, self.app_id, self.account)


@dataclass(frozen=True, slots=True)
class LogEvent:
    """One Okta system log event (`/api/v1/logs`)."""

    uuid: str
    published: str
    event_type: str
    actor: str
    actor_type: str
    via: str
    app_id: str  # the Okta app id (0oa...)
    app: str  # the catalog id, when the Okta app is known
    user_id: str
    user_login: str
