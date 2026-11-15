"""Load and validate the policy files in access/policy/."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
FILES = ("apps", "roles", "title_map", "exceptions", "break_glass", "settings")


class PolicyError(ValueError):
    """A policy file is invalid; the message says which and why."""


def default_policy_dir() -> Path:
    """access/policy/, found by walking up from this file to the access/ directory."""
    for parent in Path(__file__).resolve().parents:
        if parent.name == "access" and (parent / "policy").is_dir():
            return parent / "policy"
    raise PolicyError("can't find the access/policy directory")


@dataclass(frozen=True, slots=True)
class App:
    id: str
    name: str
    sso: bool


@dataclass(frozen=True, slots=True)
class Role:
    id: str
    title: str
    department: str
    sso_apps: tuple[str, ...]
    other_apps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TitleEntry:
    title: str
    role: str
    source: str
    reviewed_by: str


@dataclass(frozen=True, slots=True)
class PolicyException:
    employee_id: str
    app: str
    reason: str
    approval: str
    expires: date


@dataclass(frozen=True, slots=True)
class Settings:
    blast_radius_n: int
    approvals_over_n: int
    approvals_default: int
    reference_date: date
    month_start: date


@dataclass(frozen=True, slots=True)
class Policy:
    apps: Mapping[str, App]
    roles: Mapping[str, Role]
    birthright_employee: tuple[str, ...]
    birthright_contractor: tuple[str, ...]
    title_map: Mapping[str, TitleEntry]
    exceptions: tuple[PolicyException, ...]
    break_glass_accounts: frozenset[str]
    break_glass_patterns: tuple[re.Pattern[str], ...]
    settings: Settings

    def break_glass_kind(self, account: str) -> str | None:
        """ "listed", "pattern" (a full match, case-insensitive) or None."""
        name = account.strip().lower()
        if name in {a.lower() for a in self.break_glass_accounts}:
            return "listed"
        if any(p.fullmatch(name) for p in self.break_glass_patterns):
            return "pattern"
        return None

    def is_break_glass(self, account: str) -> bool:
        """True if the account is listed, or fully matches a break-glass pattern."""
        return self.break_glass_kind(account) is not None


def validate(name: str, data: Any) -> None:
    """Check `data` against the JSON schema for policy file `name`; raise PolicyError."""
    schema = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data), key=lambda e: [str(p) for p in e.path]
    )
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(top level)"
        raise PolicyError(f"{name}.yaml: {where}: {first.message}")


class _UniqueKeyLoader(yaml.SafeLoader):
    """A SafeLoader that refuses a repeated key in one mapping."""


def _construct_unique(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            repeated = key in seen
            seen.add(key)
        except TypeError:
            continue
        if repeated:
            raise yaml.YAMLError(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique,  # type: ignore[arg-type]
)


def _iso_dates(data: Any) -> Any:
    """Unquoted YAML dates load as datetime.date; turn them back into ISO strings."""
    if isinstance(data, date):
        return data.isoformat()
    if isinstance(data, dict):
        return {k: _iso_dates(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_iso_dates(v) for v in data]
    return data


def parse_yaml(text: str) -> Any:
    """safe_load with duplicate-key detection and dates as ISO strings."""
    return _iso_dates(yaml.load(text, Loader=_UniqueKeyLoader))  # noqa: S506 - a SafeLoader subclass


def _read(directory: Path, name: str) -> Any:
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise PolicyError(f"{name}.yaml is missing from {directory}")
    try:
        data = parse_yaml(path.read_text())
    except yaml.YAMLError as exc:
        raise PolicyError(f"{name}.yaml isn't valid YAML: {exc}") from exc
    validate(name, data)
    return data


def _date(text: str, where: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PolicyError(f"{where}: {text!r} isn't an ISO date") from exc


def build_policy(raw: dict[str, Any]) -> Policy:
    """Cross-check already schema-valid file contents and build a Policy."""
    apps = {a["id"]: App(a["id"], a["name"], a["sso"]) for a in raw["apps"]["apps"]}
    if len(apps) != len(raw["apps"]["apps"]):
        raise PolicyError("apps.yaml: duplicate app id")

    def need(app: str, where: str, sso: bool | None = None) -> None:
        if app not in apps:
            raise PolicyError(f"{where}: unknown app {app!r}")
        if sso is not None and apps[app].sso != sso:
            raise PolicyError(
                f"{where}: {app!r} is {'an SSO' if apps[app].sso else 'a non-SSO'} app, "
                f"expected {'an SSO' if sso else 'a non-SSO'} app"
            )

    birthright = raw["roles"]["birthright"]
    for kind in ("employee", "contractor"):
        for app in birthright[kind]:
            need(app, f"roles.yaml birthright {kind}", sso=True)
    roles: dict[str, Role] = {}
    for r in raw["roles"]["roles"]:
        if r["id"] in roles:
            raise PolicyError(f"roles.yaml: duplicate role {r['id']!r}")
        for app in r["sso_apps"]:
            need(app, f"roles.yaml role {r['id']} sso_apps", sso=True)
        for app in r["other_apps"]:
            need(app, f"roles.yaml role {r['id']} other_apps", sso=False)
        roles[r["id"]] = Role(
            r["id"], r["title"], r["department"], tuple(r["sso_apps"]), tuple(r["other_apps"])
        )
    titles: dict[str, TitleEntry] = {}
    for t in raw["title_map"]["titles"]:
        if t["role"] not in roles:
            raise PolicyError(f"title_map.yaml: title {t['title']!r} maps to unknown role")
        if t["title"] in titles:
            raise PolicyError(f"title_map.yaml: duplicate title {t['title']!r}")
        titles[t["title"]] = TitleEntry(t["title"], t["role"], t["source"], t["reviewed_by"])
    exceptions = []
    for e in raw["exceptions"]["exceptions"]:
        need(e["app"], f"exceptions.yaml ({e['employee_id']})")
        exceptions.append(
            PolicyException(
                e["employee_id"],
                e["app"],
                e["reason"],
                e["approval"],
                _date(e["expires"], "exceptions.yaml expires"),
            )
        )
    try:
        patterns = tuple(re.compile(p, re.I) for p in raw["break_glass"]["patterns"])
    except re.error as exc:
        raise PolicyError(f"break_glass.yaml: bad pattern: {exc}") from exc
    s = raw["settings"]
    if s["blast_radius_n"] <= 0:
        raise PolicyError("settings.yaml: blast_radius_n must be above 0")
    if not s["approvals_over_n"] >= s["approvals_default"] >= 1:
        raise PolicyError(
            "settings.yaml: approvals must satisfy approvals_over_n >= approvals_default >= 1"
        )
    settings = Settings(
        s["blast_radius_n"],
        s["approvals_over_n"],
        s["approvals_default"],
        _date(s["reference_date"], "settings.yaml reference_date"),
        _date(s["month_start"], "settings.yaml month_start"),
    )
    if settings.month_start > settings.reference_date:
        raise PolicyError("settings.yaml: month_start must not be after reference_date")
    return Policy(
        MappingProxyType(apps),
        MappingProxyType(roles),
        tuple(birthright["employee"]),
        tuple(birthright["contractor"]),
        MappingProxyType(titles),
        tuple(exceptions),
        frozenset(raw["break_glass"]["accounts"]),
        patterns,
        settings,
    )


def load_policy(directory: Path | None = None) -> Policy:
    """Read, schema-check and cross-check every policy file in `directory`."""
    base = directory if directory is not None else default_policy_dir()
    return build_policy({name: _read(base, name) for name in FILES})
