"""Output guard: no reply may contain another person's company email or bare username.

It catches a model that "builds" a colleague's address from first.last, copies
one from the request text, or confirms a guessed username ("yes, it is
noah.jansen") without any full address. Simple obfuscations such as "[dot]" and
"[at]" are undone before matching. It runs on every outgoing message, including
the ones written by code, so a mistake in a template cannot leak either.

The guard may also block look-alike tokens that are not real users (anything
shaped like first.last). That only causes a hand-off to a person.
"""

import re
from dataclasses import dataclass

_USERNAME = re.compile(
    r"(?<![\w.@+-])([a-z]{2,}(?:-[a-z]+)*\.[a-z]{2,}(?:-[a-z]+)*\d*)(?![\w@+-])(?!\.\w)",
    re.IGNORECASE,
)
# The last lookahead skips the start of longer dotted names such as help.meridian.example.
_WEB_TLDS = frozenset(
    {"com", "net", "org", "io", "app", "dev", "co", "de", "fr", "es", "uk", "example"}
)
_DOT = re.compile(r"\s*[\[(]dot[\])]\s*|\s+dot\s+", re.IGNORECASE)
_AT = re.compile(r"\[at\]|\(at\)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class GuardResult:
    allowed: bool
    blocked: tuple[str, ...]


def normalise(text: str) -> str:
    """Undo simple obfuscations: [dot], (dot), ' dot ' become '.'; [at], (at) become '@'."""
    return _AT.sub("@", _DOT.sub(".", text))


def check_reply(text: str, *, requester_email: str | None, domain: str) -> GuardResult:
    """Block every address at `domain`, and every bare username, that is not the requester's.

    With no requester email (an unverified sender) all of them are blocked.
    """
    text = normalise(text)
    email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@" + re.escape(domain) + r"\b", re.IGNORECASE)
    own = requester_email.lower() if requester_email is not None else None
    own_user = own.split("@", 1)[0] if own is not None else None
    blocked = {m.group(0).lower() for m in email_pattern.finditer(text)} - {own}
    for m in _USERNAME.finditer(text):
        token = m.group(1).lower()
        suffix = token.split(".", 1)[1]
        if suffix in _WEB_TLDS or token == own_user:
            continue
        blocked.add(token)
    result = tuple(sorted(blocked))
    return GuardResult(allowed=not result, blocked=result)
