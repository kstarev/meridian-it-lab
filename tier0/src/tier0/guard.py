"""Output guard: no reply may contain a company address other than the requester's.

It catches a model that "builds" a colleague's address from first.last, or
copies one from the request text. It runs on every outgoing message, including
the ones written by code, so a mistake in a template cannot leak either.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardResult:
    allowed: bool
    blocked: tuple[str, ...]


def check_reply(text: str, *, requester_email: str | None, domain: str) -> GuardResult:
    """Block every address at `domain` that is not the requester's own.

    With no requester email (an unverified sender) every company address is blocked.
    """
    pattern = re.compile(
        r"[A-Za-z0-9._%+-]+@" + re.escape(domain) + r"\b",
        re.IGNORECASE,
    )
    own = requester_email.lower() if requester_email is not None else None
    blocked = sorted({m.group(0).lower() for m in pattern.finditer(text)} - {own})
    return GuardResult(allowed=not blocked, blocked=tuple(blocked))
