"""Reading a model's reply as JSON: the one reader every tier0 parser of model replies uses.

Python's `json.loads` keeps the last of two members with one name, so
`{"plain_how_to": false, "plain_how_to": true}` reads as `{"plain_how_to": True}`. A parser that
then checks the keys sees one key with the last value, and a reply that says no and then yes
reads as yes (gap 3F-1, docs/evals/2026-11-18-safety-mutation-testing.md). A JSON schema can't
forbid a doubled name, so the schema a model is held to doesn't rule one out.

`loads_reply` refuses any object, at any depth, that names a key more than once. It raises
`DuplicateKeyError`, a `json.JSONDecodeError` (so also a `ValueError`): each parser already
reads that as a reply it can't use and fails closed, as for any other reply off the schema.
Every other text reads exactly as `json.loads` reads it, and raises what it raises.

Only model replies go through here. Files, configs and HTTP bodies are read with `json.loads`.
"""

import json
from typing import Any


class DuplicateKeyError(json.JSONDecodeError):
    """An object in the reply names `key` more than once. Where isn't kept: `pos` is 0."""

    def __init__(self, key: str) -> None:
        super().__init__(f"the key {key!r} appears more than once in one object", "", 0)
        self.key = key

    def __reduce__(self) -> tuple[type["DuplicateKeyError"], tuple[str]]:
        return type(self), (self.key,)


def _refuse_doubled(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """One object's members, in the order written, as a dict: what `json.loads` builds when no
    name is doubled."""
    members: dict[str, Any] = {}
    for key, value in pairs:
        if key in members:
            raise DuplicateKeyError(key)
        members[key] = value
    return members


def loads_reply(text: str) -> Any:
    """The reply as JSON, read as `json.loads(text)` reads it, except that an object that names
    a key more than once, at any depth, raises `DuplicateKeyError`."""
    return json.loads(text, object_pairs_hook=_refuse_doubled)
