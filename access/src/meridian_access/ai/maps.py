"""Suggested map entries (access/policy/suggested_maps.yaml).

An entry is {kind: title|account, key, value, source: ai, model, reviewed: false}. A person
reviews it in a pull request and sets `reviewed: true` (and moves it into the reviewed map).
Nothing in the desired or actual state calls anything here: only `reviewed_only` entries may
ever be used, and those are not read by this milestone's code at all.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from meridian_access.ai.matching import MatchSuggestion
from meridian_access.ai.titles import RoleSuggestion
from meridian_access.policy.load import default_policy_dir

KINDS = ("title", "account")
HEADER = (
    "# AI suggestions waiting for review (AAC-006). Fake data. An entry has no effect until a\n"
    "# person has reviewed it in a pull request and set reviewed: true.\n"
)


def default_path() -> Path:
    return default_policy_dir() / "suggested_maps.yaml"


def title_entry(title: str, suggestion: RoleSuggestion) -> dict[str, Any] | None:
    if suggestion.role == "none":
        return None
    return {
        "kind": "title",
        "key": title,
        "value": suggestion.role,
        "source": "ai",
        "model": suggestion.model,
        "reviewed": False,
    }


def account_entry(
    system: str, app_id: str, account: str, suggestion: MatchSuggestion
) -> dict[str, Any] | None:
    if suggestion.employee_id == "none":
        return None
    return {
        "kind": "account",
        "key": f"{system}/{app_id}/{account}",
        "value": suggestion.employee_id,
        "source": "ai",
        "model": suggestion.model,
        "reviewed": False,
    }


def load_entries(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or default_path()
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("entries") or []
    for e in entries:
        if not isinstance(e, Mapping) or e.get("kind") not in KINDS or "key" not in e:
            raise ValueError(f"{path}: not a map entry: {e!r}")
    return list(entries)


def write_entries(entries: Iterable[Mapping[str, Any]], path: Path | None = None) -> int:
    """Add entries not already present (same kind and key); returns how many were added.
    Existing entries, reviewed or not, are never changed."""
    path = path or default_path()
    current = load_entries(path)
    have = {(e["kind"], e["key"]) for e in current}
    added = 0
    for e in entries:
        k = (e["kind"], e["key"])
        if k not in have:
            current.append(dict(e))
            have.add(k)
            added += 1
    text = yaml.safe_dump({"entries": current}, sort_keys=True, allow_unicode=True)
    path.write_text(HEADER + text, encoding="utf-8")
    return added


def reviewed_only(entries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The only entries anything may rely on: reviewed by a person."""
    return [e for e in entries if e.get("reviewed") is True]
