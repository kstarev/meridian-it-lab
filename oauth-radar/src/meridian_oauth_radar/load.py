"""Read export-shaped files into grants. Milestone 0 reads one shape (OAR-005).

**Google token export.** Shaped like the Google Admin SDK Directory API's `tokens.list`
response (`"kind": "admin#directory#tokenList"`), with every user's tokens merged into one
`items` list, as a collector would save it. Each item names the user (`userKey`), the app
(`clientId`, `displayText`) and the scopes the user granted (`scopes`). Only those four
fields are read. Anything else in an item is ignored, so nothing else can reach a rule.

**IT's banned-app list.** A small JSON file the IT team owns:
`{"apps": [{"app_id": ..., "name": ..., "reason": ...}]}`.

A file or item that can't be read stops the load with an error that says where. Nothing is
dropped silently, because a skipped grant would be a missed finding.
"""

import json
from pathlib import Path
from typing import Any

from meridian_oauth_radar import Grant

GOOGLE_KIND = "admin#directory#tokenList"


class ExportError(ValueError):
    """A file isn't the shape the loader reads. The message says which file and where."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExportError(f"{path}: can't read it as JSON ({err})") from err


def _text(where: str, item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExportError(f'{where}: "{key}" must be a non-empty string')
    return value


def load_google_tokens(path: Path) -> tuple[Grant, ...]:
    """Every grant in a Google token export, in file order."""
    data = _read_json(path)
    if not isinstance(data, dict) or data.get("kind") != GOOGLE_KIND:
        raise ExportError(f'{path}: not a Google token export (needs "kind": "{GOOGLE_KIND}")')
    # tokens.list leaves "items" out when there are no tokens.
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ExportError(f'{path}: "items" must be a list')
    grants = []
    for index, item in enumerate(items):
        where = f"{path}: item {index}"
        if not isinstance(item, dict):
            raise ExportError(f"{where}: not an object")
        scopes = item.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(s, str) and s for s in scopes):
            raise ExportError(f'{where}: "scopes" must be a list of non-empty strings')
        grants.append(
            Grant(
                source="google",
                user=_text(where, item, "userKey"),
                app_id=_text(where, item, "clientId"),
                app_name=_text(where, item, "displayText"),
                scopes=tuple(sorted(set(scopes))),
            )
        )
    return tuple(grants)


def load_banned_apps(path: Path) -> dict[str, str]:
    """IT's banned-app list as {app_id: reason}."""
    data = _read_json(path)
    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, list):
        raise ExportError(f'{path}: not a banned-app list (needs an "apps" list)')
    banned: dict[str, str] = {}
    for index, app in enumerate(apps):
        where = f"{path}: app {index}"
        if not isinstance(app, dict):
            raise ExportError(f"{where}: not an object")
        app_id = _text(where, app, "app_id")
        if app_id in banned:
            raise ExportError(f"{where}: {app_id} is listed twice")
        banned[app_id] = _text(where, app, "reason")
    return banned
