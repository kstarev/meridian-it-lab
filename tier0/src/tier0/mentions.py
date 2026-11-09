"""Does a message mention another directory user? A code-only signal (ADR-017).

This is an extra signal, not a refusal. A namesake-only mention (for example
alex.kim2 quoting alex.kim's address) is treated as the requester quoting the
wrong address, so it gives no hand-off signal. The check is code-only: it is
never offered to the model as a tool, and it uses only the narrow, logged,
id-only users.list existence lookup.
"""

import re
from dataclasses import dataclass

from tier0.directory_api import DirectoryApi, DirectoryError
from tier0.guard import _USERNAME, _WEB_TLDS, normalise
from tier0.identity.binding import BoundIdentity

MENTION_PRINCIPAL = "tier0-mention-check"
MAX_LOOKUPS = 6

_NAME = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")


def find_candidates(text: str, domain: str) -> tuple[list[str], list[str]]:
    """Return (emails, names) that might be directory users. Pure, no I/O."""
    text = normalise(text)
    email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@" + re.escape(domain) + r"\b", re.IGNORECASE)
    emails: list[str] = []

    def add(address: str) -> None:
        if address not in emails:
            emails.append(address)

    for m in email_pattern.finditer(text):
        add(m.group(0).lower())
    for m in _USERNAME.finditer(text):
        token = m.group(1).lower()
        if token.split(".", 1)[1] in _WEB_TLDS:
            continue
        add(f"{token}@{domain.lower()}")
    names: list[str] = []
    for m in _NAME.finditer(text):
        if m.group(0) not in names:
            names.append(m.group(0))
    return emails, names


@dataclass(frozen=True, slots=True)
class MentionResult:
    others: int
    namesakes: int
    lookups: int

    @property
    def mentions_other_user(self) -> bool:
        return self.others > 0


class MentionCheck:
    """Look up the people a message names. See the module docstring."""

    def __init__(self, directory: DirectoryApi) -> None:
        self._directory = directory

    def _ids(self, query: str) -> set[str]:
        try:
            result = self._directory.users_list(
                query=query, principal=MENTION_PRINCIPAL, fields=("id",), max_results=2
            )
        except DirectoryError:
            return set()
        return {u["id"] for u in result.get("users", []) if isinstance(u.get("id"), str)}

    def check(self, text: str, requester: BoundIdentity, *, domain: str) -> MentionResult:
        emails, names = find_candidates(text, domain)
        own_email = requester.email.lower()
        own_name = requester.full_name.lower() if requester.full_name else None
        queries = [f"email:{e}" for e in emails if e != own_email]
        queries += [f"name:'{n}'" for n in names if n.lower() != own_name]
        # Keep one lookup free for the namesake question.
        budget = MAX_LOOKUPS - (1 if requester.full_name else 0)
        lookups = 0
        matched: set[str] = set()
        for query in queries[:budget]:
            lookups += 1
            matched |= self._ids(query)
        matched.discard(requester.directory_id)
        if not matched:
            return MentionResult(others=0, namesakes=0, lookups=lookups)
        namesake_ids: set[str] = set()
        if requester.full_name:
            lookups += 1
            namesake_ids = self._ids(f"name:'{requester.full_name}'")
        namesakes = matched & namesake_ids
        return MentionResult(
            others=len(matched - namesakes), namesakes=len(namesakes), lookups=lookups
        )
