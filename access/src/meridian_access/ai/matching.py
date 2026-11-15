"""Owner suggestions for accounts the rules left unmatched.

What is sent: the account's name fields and its username (or the local part of a work
address), and up to 20 candidate people (employee id, names, work-email local part,
department). Never a personal email address, never a date.
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.pricing import HAIKU
from meridian_access.models import Account
from meridian_dataset.access.model import DOMAIN, HrisRecord

MAX_CANDIDATES = 20
EVIDENCE_FIELDS = (
    "given_name",
    "family_name",
    "display_name",
    "username",
    "email_local",
    "department",
)
SYSTEM = (
    "An account in a software system could not be matched to an employee by rules. From the "
    'candidate list, pick the employee who owns it, or "none" if no candidate is clearly '
    "the owner (a guess is worse than none). List the fields you relied on. You only "
    "suggest; people review every suggestion."
)


@dataclass(frozen=True, slots=True)
class MatchSuggestion:
    employee_id: str  # a candidate id, or "none"
    evidence: tuple[str, ...]
    model: str


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _no_addresses(text: str) -> str:
    """A name with any `@...` token (an email address pasted into a name) removed."""
    return " ".join(t for t in text.split() if "@" not in t)


def account_payload(account: Account) -> dict[str, str]:
    """The only account fields the model sees."""
    username = account.account
    if "@" in username:
        local, _, domain = username.partition("@")
        username = local if domain.lower() == DOMAIN else ""
    return {
        "given_name": _no_addresses(account.given),
        "family_name": _no_addresses(account.family),
        "display_name": _no_addresses(account.display),
        "username": username,
    }


def candidate_payload(record: HrisRecord) -> dict[str, str]:
    return {
        "employee_id": record.employee_id,
        "given_name": record.given_name,
        "family_name": record.family_name,
        "preferred_name": record.preferred_name,
        "email_local": record.work_email.split("@")[0],
        "department": record.department,
    }


def shortlist(
    account: Account, records: Iterable[HrisRecord], limit: int = MAX_CANDIDATES
) -> list[HrisRecord]:
    """The `limit` records most like the account's names, best first (ties by id)."""
    payload = account_payload(account)
    probe = _fold(" ".join(v for v in payload.values() if v))
    probe_tokens = set(re.findall(r"[a-z]+", probe))

    def score(r: HrisRecord) -> float:
        text = _fold(
            f"{r.given_name} {r.preferred_name} {r.family_name} {r.work_email.split('@')[0]}"
        )
        tokens = set(re.findall(r"[a-z]+", text))
        return len(tokens & probe_tokens) + difflib.SequenceMatcher(None, probe, text).ratio()

    return sorted(records, key=lambda r: (-score(r), r.employee_id))[:limit]


def match_schema(candidate_ids: Sequence[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "employee_id": {"type": "string", "enum": [*sorted(candidate_ids), "none"]},
            "evidence": {
                "type": "array",
                "items": {"type": "string", "enum": list(EVIDENCE_FIELDS)},
            },
        },
        "required": ["employee_id", "evidence"],
        "additionalProperties": False,
    }


def match_prompt(account: Account, candidates: Sequence[HrisRecord]) -> str:
    import json

    return json.dumps(
        {
            "account": account_payload(account),
            "candidates": [candidate_payload(c) for c in candidates],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def suggest_owner(
    account: Account,
    candidates: Sequence[HrisRecord],
    client: ClaudeClient | None = None,
) -> MatchSuggestion | None:
    """One suggestion (employee id may be "none"), or None when the model refused, failed
    or gave an invalid answer. Only for accounts the rules left unmatched."""
    candidates = list(candidates)[:MAX_CANDIDATES]
    if not candidates:
        return None
    client = client or ClaudeClient(HAIKU)
    data = client.complete_json(
        SYSTEM,
        match_prompt(account, candidates),
        match_schema([c.employee_id for c in candidates]),
    )
    if data is None:
        return None
    return MatchSuggestion(data["employee_id"], tuple(data["evidence"]), client.model)
