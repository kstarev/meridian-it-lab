"""Desired state: what each HRIS person should have, from HRIS + policy + the reviewed title map.

Account states (okta, google, slack), the same meaning everywhere:
- "active": the account should exist and be active.
- "suspended": suspend it if it exists, never create it (leavers and former workers).
- "none": it should not exist; remove or deprovision it if present.
"""

import argparse
import collections
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType

from meridian_access.policy.load import Policy, load_policy
from meridian_dataset.access.model import HrisEvent, HrisRecord

CATEGORIES = ("active", "on_leave", "leaver", "former", "contractor_past_end", "superseded")
CURRENT = ("active", "on_leave")
PAST_END_ON_LEAVE = "contractor past end date while on leave"


@dataclass(frozen=True, slots=True)
class PersonDesired:
    """One person's desired state. Immutable (mappings are read-only) and hashable.

    `accounts` maps okta, google and slack to one of: "active" (should exist and be active),
    "suspended" (for leavers and former workers: suspend if it exists, never create),
    "none" (should not exist; remove or deprovision if present).
    `superseded_by` is the current worker's id when this terminated record is an old record
    of a rehired person; such a record never owns accounts.
    """

    employee_id: str
    email: str
    name: str
    role: str | None
    title: str
    employment_type: str
    status: str
    category: str
    accounts: Mapping[str, str] = field(hash=False)  # okta | google | slack -> state
    sso_apps: frozenset[str]
    other_apps: frozenset[str]
    reasons: Mapping[str, str] = field(hash=False)  # app -> birthright | role | exception
    unmapped_title: bool
    via_bulk_import: bool
    superseded_by: str = ""


@dataclass(frozen=True, slots=True)
class DesiredState:
    people: tuple[PersonDesired, ...]
    unmapped_titles: tuple[str, ...]
    as_of: date
    needs_decision: tuple[tuple[str, str], ...] = ()  # (employee_id, reason)
    unused_exceptions: tuple[tuple[str, str, str], ...] = ()  # (employee_id, app, why)

    def by_email(self) -> Mapping[str, PersonDesired]:
        """Non-superseded people by case-folded work email; ValueError on a remaining conflict."""
        found: dict[str, PersonDesired] = {}
        for p in self.people:
            if p.category == "superseded":
                continue
            key = p.email.casefold()
            if key in found:
                raise ValueError(
                    f"{found[key].employee_id} and {p.employee_id} share the email {key!r}"
                )
            found[key] = p
        return MappingProxyType(found)

    def digest(self) -> str:
        """sha256 of a canonical JSON dump; the same inputs always give the same digest."""
        people = [
            {
                "employee_id": p.employee_id,
                "email": p.email,
                "name": p.name,
                "role": p.role,
                "title": p.title,
                "employment_type": p.employment_type,
                "status": p.status,
                "category": p.category,
                "accounts": dict(p.accounts),
                "sso_apps": sorted(p.sso_apps),
                "other_apps": sorted(p.other_apps),
                "reasons": dict(p.reasons),
                "unmapped_title": p.unmapped_title,
                "via_bulk_import": p.via_bulk_import,
                "superseded_by": p.superseded_by,
            }
            for p in self.people
        ]
        body = {
            "as_of": self.as_of.isoformat(),
            "people": people,
            "unmapped_titles": list(self.unmapped_titles),
            "needs_decision": [list(x) for x in self.needs_decision],
            "unused_exceptions": [list(x) for x in self.unused_exceptions],
        }
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode()).hexdigest()

    def counts(self) -> dict[str, int]:
        tally = collections.Counter(p.category for p in self.people)
        return {c: tally.get(c, 0) for c in CATEGORIES}


def load_hris(path: Path) -> tuple[tuple[HrisRecord, ...], tuple[HrisEvent, ...]]:
    """Read an exported hris.json into the dataset's dataclasses."""
    data = json.loads(path.read_text())
    records = tuple(HrisRecord(**r) for r in data["records"])
    events = tuple(
        HrisEvent(
            day=e["day"],
            date=e["date"],
            employee_id=e["employee_id"],
            kind=e["kind"],
            fields=tuple(e["fields"].items()),
            source=e["source"],
        )
        for e in data["events"]
    )
    return records, events


def _category(r: HrisRecord, month_start: str, as_of: str) -> str:
    if r.status == "terminated":
        return "former" if r.end_date and r.end_date < month_start else "leaver"
    if r.status == "on_leave":
        return "on_leave"
    if r.employment_type == "contractor" and r.end_date and r.end_date < as_of:
        return "contractor_past_end"
    return "active"


def desired_state(
    records: Iterable[HrisRecord],
    events: Iterable[HrisEvent],
    policy: Policy,
    as_of: date | None = None,
) -> DesiredState:
    """What every HRIS person should have on `as_of` (default: the policy's reference date)."""
    day = as_of if as_of is not None else policy.settings.reference_date
    month_start = policy.settings.month_start.isoformat()
    records = tuple(records)
    latest: dict[str, tuple[int, int, str]] = {}
    for index, ev in enumerate(events):
        if ev.kind == "terminate":
            key = (ev.day, index)
            if ev.employee_id not in latest or key > latest[ev.employee_id][:2]:
                latest[ev.employee_id] = (ev.day, index, ev.source)
    bulk = {eid for eid, (_, _, source) in latest.items() if source == "bulk_import"}
    current_by_email: dict[str, str] = {}
    for r in sorted(records, key=lambda x: x.employee_id):
        if r.status in ("active", "on_leave"):
            current_by_email.setdefault(r.work_email.casefold(), r.employee_id)
    grants: dict[str, list[str]] = collections.defaultdict(list)
    for exc in policy.exceptions:
        if day <= exc.expires:
            grants[exc.employee_id].append(exc.app)

    people: list[PersonDesired] = []
    unmapped: set[str] = set()
    needs_decision: list[tuple[str, str]] = []
    for r in sorted(records, key=lambda x: x.employee_id):
        category = _category(r, month_start, day.isoformat())
        superseded_by = ""
        if r.status == "terminated" and r.work_email.casefold() in current_by_email:
            category = "superseded"
            superseded_by = current_by_email[r.work_email.casefold()]
        if (
            category == "on_leave"
            and r.employment_type == "contractor"
            and r.end_date
            and r.end_date < day.isoformat()
        ):
            needs_decision.append((r.employee_id, PAST_END_ON_LEAVE))
        name = r.preferred_name or r.given_name
        full = f"{name} {r.family_name}".strip()
        accounts = {"okta": "none", "google": "none", "slack": "none"}
        sso: set[str] = set()
        other: set[str] = set()
        reasons: dict[str, str] = {}
        role: str | None = None
        is_unmapped = False
        if category in ("active", "on_leave"):
            state = "active" if category == "active" else "suspended"
            accounts = {"okta": state, "google": state, "slack": state}
            entry = policy.title_map.get(r.job_title)
            role = entry.role if entry else None
            is_unmapped = entry is None
            if is_unmapped:
                unmapped.add(r.job_title)
            birthright = (
                policy.birthright_contractor
                if r.employment_type == "contractor"
                else policy.birthright_employee
            )
            sso.update(birthright)
            reasons.update({a: "birthright" for a in birthright})
            if role is not None:
                spec = policy.roles[role]
                for a in spec.sso_apps:
                    sso.add(a)
                    reasons.setdefault(a, "role")
                for a in spec.other_apps:
                    other.add(a)
                    reasons.setdefault(a, "role")
            for a in grants.get(r.employee_id, ()):
                (sso if policy.apps[a].sso else other).add(a)
                reasons.setdefault(a, "exception")
        elif category in ("leaver", "former"):
            accounts = {"okta": "suspended", "google": "suspended", "slack": "none"}
        people.append(
            PersonDesired(
                employee_id=r.employee_id,
                email=r.work_email,
                name=full,
                role=role,
                title=r.job_title,
                employment_type=r.employment_type,
                status=r.status,
                category=category,
                accounts=MappingProxyType(accounts),
                sso_apps=frozenset(sso),
                other_apps=frozenset(other),
                reasons=MappingProxyType(reasons),
                unmapped_title=is_unmapped,
                via_bulk_import=r.employee_id in bulk,
                superseded_by=superseded_by,
            )
        )
    status = {p.employee_id: p.category for p in people}
    unused: list[tuple[str, str, str]] = []
    for exc in policy.exceptions:
        if exc.employee_id not in status:
            why = "unknown person"
        elif status[exc.employee_id] not in CURRENT:
            why = "not a current worker"
        elif day > exc.expires:
            why = "expired"
        else:
            continue
        unused.append((exc.employee_id, exc.app, why))
    return DesiredState(
        tuple(people),
        tuple(sorted(unmapped)),
        day,
        tuple(needs_decision),
        tuple(unused),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.desired")
    parser.add_argument("--world", type=Path, required=True, help="a directory with hris.json")
    args = parser.parse_args(argv)
    records, events = load_hris(args.world / "hris.json")
    state = desired_state(records, events, load_policy())
    for category, n in state.counts().items():
        print(f"{category}: {n}")
    print(f"via_bulk_import: {sum(p.via_bulk_import for p in state.people)}")
    print(f"needs decision: {len(state.needs_decision)}")
    print(f"unmapped titles: {len(state.unmapped_titles)}")
    print(f"digest: {state.digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
