"""The guide picker: one model call that sorts a how-to message and picks a guide.

The reply is one JSON object, held to a schema where the model supports it:

    {"request_type": "how_to", "guide": "KB-014"}

Anything else is treated as "hand off": a reply that isn't that object, a guide id that isn't
in the list, or a guide picked for a request that isn't a how-to. The parser fails closed.

Milestone 3e adds two optional shapes, used only by the candidates that ask for them: a reply
with a leading "asks_for" field (what the employee is asking for), and a second, small call
that checks whether the one guide chosen fits the message ("fits": "yes" or "no").
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tier0.knowledge.prompts import ASKS_FOR
from tier0.models.base import ModelRequest, ModelResponse
from tier0.models.reply_json import loads_reply

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
    asks_for: str | None = None


def guide_list(entries: Sequence[Entry]) -> str:
    return "\n".join(f"{e.id} | {e.title} | {e.summary}" for e in entries)


def pick_schema(entries: Sequence[Entry], *, asks: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    if asks:
        properties["asks_for"] = {"type": "string", "enum": list(ASKS_FOR)}
    properties["request_type"] = {"type": "string", "enum": list(REQUEST_TYPES)}
    properties["guide"] = {"type": "string", "enum": [*(e.id for e in entries), NO_GUIDE]}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def pick_request(
    text: str, entries: Sequence[Entry], system: str, *, asks: bool = False
) -> ModelRequest:
    """The model call for one message. The message is untrusted and goes in the user turn,
    inside its own tags; the rules and the guide list go in the system prompt."""
    return ModelRequest(
        task="guide_pick",
        system=f"{system}\n\nThe approved guides (id | title | what it covers):\n"
        f"{guide_list(entries)}",
        user=f"Message from the employee:\n<message>\n{text}\n</message>",
        json_schema=pick_schema(entries, asks=asks),
        max_tokens=80 if asks else 64,
    )


_UNREADABLE = object()


def _loads(text: str) -> Any:
    """The reply as JSON, or _UNREADABLE when it can't be read safely. Besides bad JSON, that is
    a number too long for Python to convert (ValueError), nesting too deep (RecursionError) and
    an object that names a key twice (loads_reply's DuplicateKeyError, gap 3F-1)."""
    try:
        return loads_reply(text)
    except (ValueError, RecursionError, TypeError):  # JSONDecodeError is a ValueError
        return _UNREADABLE


def parse_pick(response: ModelResponse, entries: Sequence[Entry], *, asks: bool = False) -> Pick:
    """Strict: any reply that isn't exactly the expected object is an off-format hand-off."""
    off = Pick(request_type=OTHER, guide=None, off_format=True)
    if response.finish_reason != "stop":
        return off
    data = _loads(response.text)
    if data is _UNREADABLE:
        return off
    keys = {"asks_for", "request_type", "guide"} if asks else {"request_type", "guide"}
    if not isinstance(data, dict) or set(data) != keys:
        return off
    request_type, guide = data["request_type"], data["guide"]
    asks_for = data["asks_for"] if asks else None
    if asks and asks_for not in ASKS_FOR:
        return off
    if request_type not in REQUEST_TYPES or not isinstance(guide, str):
        return off
    if guide == NO_GUIDE:
        return Pick(request_type=request_type, guide=None, asks_for=asks_for)
    if guide not in {e.id for e in entries}:
        return off
    return Pick(request_type=request_type, guide=guide, asks_for=asks_for)


FITS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"fits": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["fits"],
    "additionalProperties": False,
}


def fit_request(text: str, entry: Entry, system: str) -> ModelRequest:
    """The second call of the fit check: the message and the one guide's line, never its steps."""
    return ModelRequest(
        task="guide_fit",
        system=system,
        user=f"Message from the employee:\n<message>\n{text}\n</message>\n\n"
        "The guide chosen (id | title | what it covers):\n"
        f"<guide>\n{entry.id} | {entry.title} | {entry.summary}\n</guide>",
        json_schema=FITS_SCHEMA,
        max_tokens=16,
    )


def parse_fit(response: ModelResponse) -> bool | None:
    """Strict: True for "yes", False for "no", None for anything else."""
    if response.finish_reason != "stop":
        return None
    data = _loads(response.text)
    if data is _UNREADABLE:
        return None
    if not isinstance(data, dict) or set(data) != {"fits"}:
        return None
    if data["fits"] == "yes":
        return True
    if data["fits"] == "no":
        return False
    return None
