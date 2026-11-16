"""Remove personal details before anything reaches the drafting model (ADR-035, ADR-005).

Two layers, applied in this order:

- **The directory.** Every person's full, given and family name, email address, username (the
  email address and its local part, as in `alex.kim2`), Slack id and directory id, the Slack
  team's id, and the Slack Connect users. They are found in any case, as whole words.
- **Patterns,** for what no directory lists: any email address, Slack mentions and Slack-style
  ids, @handles, ticket and record ids (TKT-0042, EMP-1234), device ids (asset tags, serial
  numbers, MAC and IP addresses), and phone numbers or other long numbers.

Each detail is replaced with a marker such as [name] or [email]. `find_personal` is the check
behind it: it looks for the same details in text that should hold none (the drafting request
before it is sent, and the draft before it is proposed), and it returns kinds only, never the
detail it found.

Known limit: a name that is in no directory (someone outside the company, a nickname) is
caught only when it comes with a pattern, such as an email address. That is one reason the
drafting request also leaves out every field it doesn't need: who asked, who answered, when,
and which ticket.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from meridian_dataset import World

NAME = "name"
EMAIL = "email"
USERNAME = "username"
SLACK_ID = "slack_id"
DIRECTORY_ID = "directory_id"
TICKET_ID = "ticket_id"
DEVICE_ID = "device_id"
PHONE = "phone"
NUMBER = "number"
MARKER = "marker"
KINDS = (
    NAME,
    EMAIL,
    USERNAME,
    SLACK_ID,
    DIRECTORY_ID,
    TICKET_ID,
    DEVICE_ID,
    PHONE,
    NUMBER,
    MARKER,
)
MARKERS = {
    NAME: "[name]",
    EMAIL: "[email]",
    USERNAME: "[username]",
    SLACK_ID: "[slack-id]",
    DIRECTORY_ID: "[id]",
    TICKET_ID: "[ticket]",
    DEVICE_ID: "[device]",
    PHONE: "[phone]",
    NUMBER: "[number]",
}

_MENTION = re.compile(r"<[@#!][A-Z0-9]+(?:\|[^>]*)?>")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ASSET = re.compile(r"\b(?:MER|MRD|LT|NB|PC|MBP|ASSET|DEV|HW)-[A-Z0-9-]*\d[A-Z0-9-]*\b", re.I)
_SERIAL = re.compile(
    r"\b(serial(?: number| no\.?)?|s/n|seriennummer|n[uú]mero de serie|num[eé]ro de s[eé]rie)"
    r"(\s*(?:is|ist|es|est)?\s*[:#]?\s*)((?=[A-Za-z0-9-]{4,})[A-Za-z0-9-]*\d[A-Za-z0-9-]*)",
    re.I,
)
_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,8}\b")
_SLACK_LIKE = re.compile(r"\b[UWT](?=[A-Z0-9]*\d)[A-Z0-9]{8,11}\b")
_SERIAL_LIKE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,16}\b")
_PHONE = re.compile(r"(?<![\w+])(?:\+|00|0)\d[\d\s().-]{5,}\d(?!\w)|\(\d[\d\s().-]{5,}\d(?!\w)")
_LONG_NUMBER = re.compile(r"(?<!\w)\d[\d\s.-]{5,}\d(?!\w)")
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z][\w.-]{1,40}")
_MARKER = re.compile(r"\[(?:" + "|".join(re.escape(m[1:-1]) for m in MARKERS.values()) + r")\]")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _digits(text: str) -> int:
    return sum(c.isdigit() for c in text)


def _words(values: Iterable[str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern for whole-word matches, longest first, accents optional."""
    forms: set[str] = set()
    for value in values:
        value = value.strip()
        if len(value) >= 2:
            forms.update({value, _fold(value)})
    if not forms:
        return None
    body = "|".join(re.escape(v) for v in sorted(forms, key=lambda v: (-len(v), v)))
    return re.compile(rf"(?<![\w.@-])(?:{body})(?![\w@]|\.\w|-\w)", re.I)


@dataclass(frozen=True, slots=True)
class PersonalDetails:
    """What the directory knows about people, for finding them in text. Built from the world,
    which stands in for the Google directory and Slack (ADR-006). Never sent anywhere."""

    names: tuple[str, ...]
    emails: tuple[str, ...]
    usernames: tuple[str, ...]
    slack_ids: tuple[str, ...]
    directory_ids: tuple[str, ...]

    @classmethod
    def from_world(cls, world: World, extra_names: Iterable[str] = ()) -> "PersonalDetails":
        names: list[str] = list(extra_names)
        emails: list[str] = []
        usernames: list[str] = []
        slack_ids: list[str] = [world.slack_team.team_id]
        directory_ids: list[str] = []
        for p in world.people:
            names += [p.full_name, p.given_name, p.family_name]
            emails.append(p.primary_email)
            usernames.append(p.primary_email.split("@", 1)[0])
            slack_ids.append(p.slack_user_id)
            directory_ids.append(p.directory_id)
        for u in world.external_slack_users:
            names += [u.real_name, *u.real_name.split()]
            emails.append(u.email)
            usernames += [u.display_name, u.email.split("@", 1)[0]]
            slack_ids += [u.slack_user_id, u.team_id]

        def unique(values: list[str]) -> tuple[str, ...]:
            return tuple(sorted({v for v in values if v}))

        return cls(
            names=unique(names),
            emails=unique(emails),
            usernames=unique(usernames),
            slack_ids=unique(slack_ids),
            directory_ids=unique(directory_ids),
        )


class Scrubber:
    """Replaces personal details with markers. Build once per run; `scrub` is pure."""

    def __init__(self, details: PersonalDetails) -> None:
        self.details = details
        self._known: tuple[tuple[str, re.Pattern[str] | None], ...] = (
            (EMAIL, _words(details.emails)),
            (USERNAME, _words(details.usernames)),
            (SLACK_ID, _exact(details.slack_ids)),
            (DIRECTORY_ID, _exact(details.directory_ids)),
            (NAME, _words(details.names)),
        )

    def scrub(self, text: str) -> str:
        """The text with every personal detail it can find replaced by a marker."""
        text = _MENTION.sub(MARKERS[SLACK_ID], text)
        text = _EMAIL.sub(MARKERS[EMAIL], text)
        for kind, pattern in self._known:
            if pattern is not None:
                text = pattern.sub(MARKERS[kind], text)
        text = _SERIAL.sub(lambda m: f"{m.group(1)}{m.group(2)}{MARKERS[DEVICE_ID]}", text)
        text = _MAC.sub(MARKERS[DEVICE_ID], text)
        text = _IPV4.sub(MARKERS[DEVICE_ID], text)
        text = _ASSET.sub(MARKERS[DEVICE_ID], text)
        text = _TICKET.sub(MARKERS[TICKET_ID], text)
        text = _SLACK_LIKE.sub(MARKERS[SLACK_ID], text)
        text = _SERIAL_LIKE.sub(MARKERS[DEVICE_ID], text)
        text = _PHONE.sub(lambda m: _number(m.group(0), PHONE), text)
        text = _LONG_NUMBER.sub(lambda m: _number(m.group(0), NUMBER), text)
        return _HANDLE.sub(MARKERS[USERNAME], text)

    def find(self, text: str, *, markers: bool = False) -> tuple[str, ...]:
        """The kinds of personal detail in `text`, sorted; () when there are none.

        Stricter than `scrub`: every known email address, username, Slack id and directory
        id is also looked for as a plain substring, in any case. With `markers`, a marker
        such as [name] counts too (a draft must never copy one)."""
        found: set[str] = set()
        if _MENTION.search(text):
            found.add(SLACK_ID)
        if _EMAIL.search(text):
            found.add(EMAIL)
        for kind, pattern in self._known:
            if pattern is not None and pattern.search(text):
                found.add(kind)
        lowered = text.lower()
        for kind, values in (
            (EMAIL, self.details.emails),
            (USERNAME, self.details.usernames),
            (SLACK_ID, self.details.slack_ids),
            (DIRECTORY_ID, self.details.directory_ids),
        ):
            if any(len(v) >= 6 and v.lower() in lowered for v in values):
                found.add(kind)
        for kind, pattern in (
            (DEVICE_ID, _SERIAL),
            (DEVICE_ID, _MAC),
            (DEVICE_ID, _IPV4),
            (DEVICE_ID, _ASSET),
            (TICKET_ID, _TICKET),
            (SLACK_ID, _SLACK_LIKE),
            (DEVICE_ID, _SERIAL_LIKE),
            (USERNAME, _HANDLE),
        ):
            if pattern.search(text):
                found.add(kind)
        for pattern, kind in ((_PHONE, PHONE), (_LONG_NUMBER, NUMBER)):
            if any(_digits(m.group(0)) >= 7 for m in pattern.finditer(text)):
                found.add(kind)
        if markers and _MARKER.search(text):
            found.add(MARKER)
        return tuple(sorted(found))


def _exact(values: Iterable[str]) -> re.Pattern[str] | None:
    values = sorted({v for v in values if v}, key=lambda v: (-len(v), v))
    if not values:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(v) for v in values) + r")(?![\w])")


def _number(found: str, kind: str) -> str:
    """A phone number or a long number has at least 7 digits; shorter runs (an error code, a
    year, a size) are kept."""
    return MARKERS[kind] if _digits(found) >= 7 else found
