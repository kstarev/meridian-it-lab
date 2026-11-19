"""The enricher: what IT and the vendor directory know about each app (milestone 1).

The catalogue's enricher gives "vendor, what the scopes allow in plain English, data
location, whether an approved alternative exists". The plain English is the scope explainer
(`scopes`). The rest comes from three files the tuning world writes under `radar/`:

- `app_info.json`: a vendor directory, with Google's verified-publisher flag:
  `{"apps": [{"client_id", "name", "vendor", "category", "ai", "verified_publisher",
  "data_location", "vendor_maturity"}]}`. Its `ai` flag is the rubric's AI signal (OAR-008).
  An app missing from it is unknown: no vendor, no AI flag, and its publisher counts as not
  verified.
- `it_app_catalogue.json`: the apps IT has reviewed: `{"apps": [{"client_id", "name",
  "category", "ai", "status", "reviewed_on"}]}`. `status` is "approved" or "not approved".
- `banned_apps.json`: IT's banned list, in the milestone-0 shape (`load.load_banned_apps`).

An approved alternative is an app IT approved in the same category as the app, other than
the app itself. The enricher reads files and returns facts. It never calls anything.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian_oauth_radar.load import ExportError, load_banned_apps

APPROVED = "approved"
STATUSES = (APPROVED, "not approved")
APP_INFO_FILE = "app_info.json"
CATALOGUE_FILE = "it_app_catalogue.json"
BANNED_FILE = "banned_apps.json"


@dataclass(frozen=True, slots=True)
class VendorEntry:
    client_id: str
    name: str
    vendor: str
    category: str
    ai: bool
    verified_publisher: bool
    data_location: str
    vendor_maturity: str


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    client_id: str
    name: str
    category: str
    ai: bool
    status: str


@dataclass(frozen=True, slots=True)
class Knowledge:
    """Everything the enricher knows, by client ID."""

    vendors: Mapping[str, VendorEntry]
    catalogue: Mapping[str, CatalogueEntry]
    banned: Mapping[str, str]

    def facts(self, client_id: str) -> "AppFacts":
        vendor = self.vendors.get(client_id)
        reviewed = self.catalogue.get(client_id)
        category = reviewed.category if reviewed else (vendor.category if vendor else None)
        alternative = None
        if category is not None and not (reviewed and reviewed.status == APPROVED):
            options = sorted(
                (entry.name, entry.client_id)
                for entry in self.catalogue.values()
                if entry.status == APPROVED
                and entry.category == category
                and entry.client_id != client_id
            )
            alternative = options[0][0] if options else None
        return AppFacts(
            client_id=client_id,
            vendor=vendor.vendor if vendor else None,
            category=category,
            verified_publisher=vendor.verified_publisher if vendor else None,
            data_location=vendor.data_location if vendor else None,
            vendor_maturity=vendor.vendor_maturity if vendor else None,
            in_it_catalogue=reviewed is not None,
            it_status=reviewed.status if reviewed else None,
            it_ai_tag=reviewed.ai if reviewed else None,
            vendor_ai=vendor.ai if vendor else None,
            banned_reason=self.banned.get(client_id),
            approved_alternative=alternative,
        )


@dataclass(frozen=True, slots=True)
class AppFacts:
    """What is known about one app. None means nobody knows."""

    client_id: str
    vendor: str | None
    category: str | None
    verified_publisher: bool | None
    data_location: str | None
    vendor_maturity: str | None
    in_it_catalogue: bool
    it_status: str | None
    it_ai_tag: bool | None
    vendor_ai: bool | None
    banned_reason: str | None
    approved_alternative: str | None


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExportError(f"{path}: can't read it as JSON ({err})") from err


def _rows(path: Path) -> list[dict[str, Any]]:
    data = _read(path)
    rows = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ExportError(f'{path}: needs an "apps" list of objects')
    return rows


def _field(path: Path, index: int, row: Mapping[str, Any], key: str, kind: type) -> Any:
    value = row.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise ExportError(f'{path}: app {index}: "{key}" must be a {kind.__name__}')
    return value


def load_knowledge(directory: Path) -> Knowledge:
    """Read the vendor directory, IT's catalogue and the banned list from one folder."""
    vendors: dict[str, VendorEntry] = {}
    path = directory / APP_INFO_FILE
    for i, row in enumerate(_rows(path)):
        entry = VendorEntry(
            client_id=_field(path, i, row, "client_id", str),
            name=_field(path, i, row, "name", str),
            vendor=_field(path, i, row, "vendor", str),
            category=_field(path, i, row, "category", str),
            ai=_field(path, i, row, "ai", bool),
            verified_publisher=_field(path, i, row, "verified_publisher", bool),
            data_location=_field(path, i, row, "data_location", str),
            vendor_maturity=_field(path, i, row, "vendor_maturity", str),
        )
        if entry.client_id in vendors:
            raise ExportError(f"{path}: app {i}: {entry.client_id} is listed twice")
        vendors[entry.client_id] = entry
    catalogue: dict[str, CatalogueEntry] = {}
    path = directory / CATALOGUE_FILE
    for i, row in enumerate(_rows(path)):
        reviewed = CatalogueEntry(
            client_id=_field(path, i, row, "client_id", str),
            name=_field(path, i, row, "name", str),
            category=_field(path, i, row, "category", str),
            ai=_field(path, i, row, "ai", bool),
            status=_field(path, i, row, "status", str),
        )
        if reviewed.status not in STATUSES:
            raise ExportError(f'{path}: app {i}: "status" must be one of {STATUSES}')
        if reviewed.client_id in catalogue:
            raise ExportError(f"{path}: app {i}: {reviewed.client_id} is listed twice")
        catalogue[reviewed.client_id] = reviewed
    return Knowledge(vendors, catalogue, load_banned_apps(directory / BANNED_FILE))


__all__ = ["APPROVED", "AppFacts", "CatalogueEntry", "Knowledge", "VendorEntry", "load_knowledge"]
