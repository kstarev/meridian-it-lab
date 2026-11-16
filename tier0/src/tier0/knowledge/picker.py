"""The guide picker: one model call that sorts a how-to message and picks a guide.

The reply is one JSON object, held to a schema where the model supports it:

    {"request_type": "how_to", "guide": "KB-014"}

Anything else is treated as "hand off": a reply that isn't that object, a guide id that isn't
in the list, or a guide picked for a request that isn't a how-to. The parser fails closed.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tier0.models.base import ModelRequest, ModelResponse

HOW_TO = "how_to"
ACCOUNT_OR_ACCESS = "account_or_access"
SOMEONE_ELSE = "someone_else"
INCIDENT = "incident"
OTHER = "other"
REQUEST_TYPES = (HOW_TO, ACCOUNT_OR_ACCESS, SOMEONE_ELSE, INCIDENT, OTHER)
NO_GUIDE = "none"


@dataclass(frozen=True, slots=True)
class Entry:
    """One guide in the list the model sees: its id, title and one-line summary, never its
    steps. The eval shows the model exactly this too (the owner's ruling, 2026-11-16)."""

    id: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class Pick:
    """What the model said, after strict parsing. `guide` is None for "none"."""

    request_type: str
    guide: str | None
    off_format: bool = False


def guide_list(entries: Sequence[Entry]) -> str:
    return "\n".join(f"{e.id} | {e.title} | {e.summary}" for e in entries)


def pick_schema(entries: Sequence[Entry]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "request_type": {"type": "string", "enum": list(REQUEST_TYPES)},
            "guide": {"type": "string", "enum": [*(e.id for e in entries), NO_GUIDE]},
        },
        "required": ["request_type", "guide"],
        "additionalProperties": False,
    }


def pick_request(text: str, entries: Sequence[Entry], system: str) -> ModelRequest:
    """The model call for one message. The message is untrusted and goes in the user turn,
    inside its own tags; the rules and the guide list go in the system prompt."""
    return ModelRequest(
        task="guide_pick",
        system=f"{system}\n\nThe approved guides (id | title | what it covers):\n"
        f"{guide_list(entries)}",
        user=f"Message from the employee:\n<message>\n{text}\n</message>",
        json_schema=pick_schema(entries),
        max_tokens=64,
    )


def parse_pick(response: ModelResponse, entries: Sequence[Entry]) -> Pick:
    """Strict: any reply that isn't exactly the expected object is an off-format hand-off."""
    off = Pick(request_type=OTHER, guide=None, off_format=True)
    if response.finish_reason != "stop":
        return off
    try:
        data = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        return off
    if not isinstance(data, dict) or set(data) != {"request_type", "guide"}:
        return off
    request_type, guide = data["request_type"], data["guide"]
    if request_type not in REQUEST_TYPES or not isinstance(guide, str):
        return off
    if guide == NO_GUIDE:
        return Pick(request_type=request_type, guide=None)
    if guide not in {e.id for e in entries}:
        return off
    return Pick(request_type=request_type, guide=guide)
