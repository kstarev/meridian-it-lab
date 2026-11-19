"""The scope explainer: what each Google OAuth scope lets an app do, in one plain sentence.

A static table, reviewed by a person, from scope URL to:

- **what the app can do**, as a short phrase that finishes "This app can ..." (for example
  "read, send and delete all your email");
- **the data it reaches** (mail, files, calendar, ...);
- **Google's class** for the scope: `restricted` (Gmail and Drive scopes that reach all of a
  person's mail or files), `sensitive` (calendar, contacts, Sheets, Docs, send-only mail and
  the like), `non-sensitive` (sign-in, and files the app made or was handed), or `admin`
  (the radar's own class, not Google's: Admin SDK scopes that reach other people's accounts).

A scope the table doesn't know is never guessed at: `explain` says so plainly, and
`google_class` returns `unknown`.

**Checked on 2026-11-19 against Google's public pages,** each cited per scope in
`oauth-radar/docs/scope-classes.md` (`CLASSES_CHECKED_ON`). No class was wrong. Every
restricted entry is on Google's restricted list (https://support.google.com/cloud/answer/13464325)
and no other entry is. Gmail's, Drive's, Docs', Sheets' and Slides' classes match their scope
pages. A few classes no public page confirms (`contacts.readonly`, `directory.readonly`,
`tasks`, the `userinfo.*` forms of the sign-in scopes, and the Admin SDK scopes) stand as
written; that page says which.

The explainer only describes. It doesn't score, decide or call anything. All data is fake;
the scope URLs are Google's real ones, because that is what a grant holds.
"""

from dataclasses import dataclass

RESTRICTED = "restricted"
SENSITIVE = "sensitive"
NON_SENSITIVE = "non-sensitive"
ADMIN = "admin"
UNKNOWN = "unknown"
CLASSES = (RESTRICTED, SENSITIVE, NON_SENSITIVE, ADMIN)

G = "https://www.googleapis.com/auth/"
# When the classes were last checked against Google's lists (oauth-radar/docs/scope-classes.md).
CLASSES_CHECKED_ON = "2026-11-19"


@dataclass(frozen=True, slots=True)
class Scope:
    url: str
    can: str  # finishes "This app can ...": lower case, no full stop
    reaches: str
    google_class: str


_TABLE: tuple[Scope, ...] = (
    # Sign-in and profile
    Scope("openid", "confirm who you are when you sign in", "sign-in", NON_SENSITIVE),
    Scope("email", "see your email address", "sign-in", NON_SENSITIVE),
    Scope("profile", "see your name and profile picture", "sign-in", NON_SENSITIVE),
    Scope(G + "userinfo.email", "see your email address", "sign-in", NON_SENSITIVE),
    Scope(G + "userinfo.profile", "see your name and profile picture", "sign-in", NON_SENSITIVE),
    # Gmail
    Scope(
        "https://mail.google.com/",
        "read, send and delete all your email, and change your mail settings",
        "mail",
        RESTRICTED,
    ),
    Scope(G + "gmail.readonly", "read all your email and its attachments", "mail", RESTRICTED),
    Scope(
        G + "gmail.modify",
        "read, change and send all your email (but not delete it for good)",
        "mail",
        RESTRICTED,
    ),
    Scope(
        G + "gmail.compose",
        "read your drafts, and write and send email as you",
        "mail",
        RESTRICTED,
    ),
    Scope(G + "gmail.insert", "add email to your mailbox", "mail", RESTRICTED),
    Scope(
        G + "gmail.metadata",
        "see who all your email is from and to, its subjects and dates, but not the text",
        "mail",
        RESTRICTED,
    ),
    Scope(
        G + "gmail.settings.basic",
        "change your mail settings and filters",
        "mail",
        RESTRICTED,
    ),
    Scope(
        G + "gmail.settings.sharing",
        "change who can reach your mail, including forwarding all of it to another address",
        "mail",
        RESTRICTED,
    ),
    Scope(G + "gmail.send", "send email as you (but not read your mail)", "mail", SENSITIVE),
    Scope(G + "gmail.labels", "see and change your email labels", "mail", NON_SENSITIVE),
    # Drive and files
    Scope(G + "drive", "see, change, share and delete all your Drive files", "files", RESTRICTED),
    Scope(G + "drive.readonly", "see and download all your Drive files", "files", RESTRICTED),
    Scope(
        G + "drive.metadata.readonly",
        "see the names, owners and details of all your Drive files, but not what's in them",
        "files",
        RESTRICTED,
    ),
    Scope(
        G + "drive.file",
        "see and change only the Drive files you open or create with this app",
        "files",
        NON_SENSITIVE,
    ),
    # Docs, Sheets, Slides
    Scope(
        G + "documents", "see, change, create and delete all your Google Docs", "files", SENSITIVE
    ),
    Scope(G + "documents.readonly", "see all your Google Docs", "files", SENSITIVE),
    Scope(
        G + "spreadsheets",
        "see, change, create and delete all your Google Sheets",
        "files",
        SENSITIVE,
    ),
    Scope(G + "spreadsheets.readonly", "see all your Google Sheets", "files", SENSITIVE),
    Scope(
        G + "presentations",
        "see, change, create and delete all your Google Slides",
        "files",
        SENSITIVE,
    ),
    # Calendar, contacts, tasks
    Scope(
        G + "calendar",
        "see, change, share and delete all your calendars",
        "calendar",
        SENSITIVE,
    ),
    Scope(G + "calendar.readonly", "see all your calendars and events", "calendar", SENSITIVE),
    Scope(
        G + "calendar.events",
        "see, change and delete events on all your calendars",
        "calendar",
        SENSITIVE,
    ),
    Scope(
        G + "calendar.events.readonly", "see events on all your calendars", "calendar", SENSITIVE
    ),
    Scope(G + "contacts", "see, change and delete your contacts", "contacts", SENSITIVE),
    Scope(G + "contacts.readonly", "see and download your contacts", "contacts", SENSITIVE),
    Scope(
        G + "directory.readonly",
        "see the company directory: everyone's names, email addresses and job details",
        "directory",
        SENSITIVE,
    ),
    Scope(G + "tasks", "see, change and delete your tasks", "tasks", SENSITIVE),
    # Admin SDK: only works for an administrator, and then reaches everyone
    Scope(
        G + "admin.directory.user",
        "create, change and delete the accounts of everyone at the company",
        "admin",
        ADMIN,
    ),
    Scope(
        G + "admin.directory.user.readonly",
        "see the accounts of everyone at the company",
        "admin",
        ADMIN,
    ),
    Scope(
        G + "admin.directory.group",
        "manage the company's groups and who is in them",
        "admin",
        ADMIN,
    ),
)

SCOPES: dict[str, Scope] = {scope.url: scope for scope in _TABLE}
if len(SCOPES) != len(_TABLE):
    raise AssertionError("a scope URL is listed twice")


def known(url: str) -> bool:
    return url in SCOPES


def can(url: str) -> str:
    """What the scope lets the app do, finishing "This app can ...", or that it's unknown."""
    scope = SCOPES.get(url)
    if scope is None:
        return f"do something the radar has no description for ({url}), so IT should check it"
    return scope.can


def explain(url: str) -> str:
    """One plain-English sentence on what the scope lets an app do."""
    if url not in SCOPES:
        return (
            f"Unknown scope: the radar has no plain-English description of {url}, "
            "so a person in IT should check what it allows."
        )
    return f"This app can {SCOPES[url].can}."


def google_class(url: str) -> str:
    """Google's class for the scope, or "unknown" when the table doesn't know it."""
    scope = SCOPES.get(url)
    return scope.google_class if scope else UNKNOWN


def reaches(url: str) -> str:
    scope = SCOPES.get(url)
    return scope.reaches if scope else UNKNOWN


def is_restricted(url: str) -> bool:
    """True only for a scope the table lists as restricted. An unknown scope isn't."""
    return google_class(url) == RESTRICTED


__all__ = [
    "ADMIN",
    "CLASSES",
    "CLASSES_CHECKED_ON",
    "NON_SENSITIVE",
    "RESTRICTED",
    "SCOPES",
    "SENSITIVE",
    "UNKNOWN",
    "Scope",
    "can",
    "explain",
    "google_class",
    "is_restricted",
    "known",
    "reaches",
]
