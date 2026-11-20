"""The one reader of a model's reply in Access-as-Code (`ClaudeClient.complete_json`).

`json.loads` keeps the last of two members that share a name, so the matcher's reply
`{"employee_id": "none", "employee_id": "E-1042", ...}` reads as a match to E-1042, and the
schema check after it sees one valid key. A JSON schema has no way to forbid a doubled name, so
structured output doesn't rule one out either.

`loads_reply` refuses any object, at any depth, that names a key more than once. It raises
`DoubledKeyError`, a `json.JSONDecodeError` and so a `ValueError`: the client already reads
that as malformed JSON and gives None, as for any reply off the schema. Any other text reads
exactly as `json.loads` reads it, and raises what it raises.

The same idea as tier0's reply reader (proposed in lab PR #154), written again here: Access is
its own package and imports nothing from tier0. Only model replies are read here; files,
configs, exports and HTTP bodies keep `json.loads`.
"""

import json
from typing import Any


class DoubledKeyError(json.JSONDecodeError):
    """One object in a reply names `key` twice. The reader doesn't know where: `pos` is 0."""

    def __init__(self, key: str) -> None:
        super().__init__(f"one object names the key {key!r} more than once", "", 0)
        self.key = key

    def __reduce__(self) -> tuple[type["DoubledKeyError"], tuple[str]]:
        # JSONDecodeError rebuilds itself from (msg, doc, pos); this one takes only the key.
        return type(self), (self.key,)


def _one_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An object's members as a dict, in the order written (what `json.loads` builds), unless
    a name comes twice."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DoubledKeyError(key)
        out[key] = value
    return out


def loads_reply(text: str) -> Any:
    """`json.loads(text)`, except that an object naming a key twice, at any depth, raises
    `DoubledKeyError`."""
    return json.loads(text, object_pairs_hook=_one_object)
