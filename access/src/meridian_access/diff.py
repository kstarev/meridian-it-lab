"""The diff: desired state against actual state gives the changes a plan proposes.

Rules follow the public expected-action rules (dataset/ACCESS_RECIPE.md). The planner never
reads the answer key. Matching never guesses: an ambiguous account is a warning, never an
orphan, and a change that rests on an unreviewed AI suggestion never appears.
"""

import collections
import dataclasses
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meridian_access.collect import ActualState
from meridian_access.desired import DesiredState, PersonDesired
from meridian_access.match import (
    Match,
    MatchResult,
    _break_glass,
    _letters,
    _split_number,
    fold,
    username_templates,
)
from meridian_access.models import Account
from meridian_access.policy.load import Policy
from meridian_dataset.access.model import DOMAIN, HrisEvent, HrisRecord

TYPE_ORDER = (
    "break_glass",
    "hris_mass_termination",
    "hr_data_error",
    "leaver_access_left",
    "orphaned_account",
    "contractor_past_end",
    "leave_of_absence",
    "mover_stale_access",
    "rehire",
    "drift",
)
DIRECTORY = ("okta", "google", "slack")
REMOVING = ("remove", "suspend")
REVIEWED_RULE = "reviewed_map"
# A same-day, same-source batch of terminations is only a "mass" termination from this many
# people up; two people with many apps each are two ordinary leavers.
MIN_BATCH_PEOPLE = 5


@dataclass(frozen=True, slots=True)
class Change:
    type: str
    employee_id: str
    account: str
    system: str  # okta | google | slack | hris | an export app id
    app_id: str
    action: str
    reason: str
    evidence: tuple[str, ...] = ()

    def sort_key(self) -> tuple[Any, ...]:
        order = TYPE_ORDER.index(self.type) if self.type in TYPE_ORDER else len(TYPE_ORDER)
        return (
            order,
            self.employee_id,
            self.system,
            self.app_id,
            self.account.strip().casefold(),
            self.action,
        )


@dataclass(frozen=True, slots=True)
class Changes:
    changes: tuple[Change, ...]
    held: tuple[Change, ...] = ()  # changes dropped because a bulk HR change is blocked
    warnings: tuple[str, ...] = ()
    suggested_map_entries: tuple[Mapping[str, Any], ...] = ()


def _norm(text: str) -> str:
    return text.strip().casefold()


def _is_export(a: Account) -> bool:
    return a.system not in DIRECTORY


def apply_reviewed_map(
    matches: MatchResult,
    entries: Iterable[Mapping[str, Any]],
    valid_ids: Collection[str] | None = None,
) -> tuple[MatchResult, list[Mapping[str, Any]], list[str]]:
    """Use reviewed account entries to place unmatched or ambiguous accounts; return the
    new result, the unreviewed entries (suggestions) and warnings. A match whose rule says it
    came from an AI is dropped unless a reviewed entry backs it. A reviewed entry that points
    at an unknown or superseded employee id (when `valid_ids` is given) is not used: it
    warns, and an account it would have placed stays out of the changes."""
    entries = list(entries)
    warnings: list[str] = []
    reviewed: dict[str, str] = {}
    for e in entries:
        if e.get("kind") != "account" or e.get("reviewed") is not True:
            continue
        value = str(e["value"])
        if valid_ids is not None and value not in valid_ids:
            warnings.append(
                f"reviewed map entry {e['key']} points at {value}, "
                "an unknown or superseded employee id; not used"
            )
            continue
        reviewed[e["key"]] = value
    bad_refs = {
        str(e["key"])
        for e in entries
        if e.get("kind") == "account"
        and e.get("reviewed") is True
        and str(e["key"]) not in reviewed
    }
    suggestions = [e for e in entries if e.get("reviewed") is not True]
    found = dict(matches.matches)
    unmatched: list[tuple[str, str, str]] = []
    ambiguous: list[tuple[str, str, str]] = []
    for key in matches.unmatched:
        ref = "/".join(key)
        if ref in reviewed:
            found[key] = Match(reviewed[ref], REVIEWED_RULE, 1.0)
        elif ref in bad_refs:
            ambiguous.append(key)  # a bad entry: hold the account, never orphan it
        else:
            unmatched.append(key)
    for key in matches.ambiguous:
        ref = "/".join(key)
        if ref in reviewed:
            found[key] = Match(reviewed[ref], REVIEWED_RULE, 1.0)
        else:
            ambiguous.append(key)
    for key, m in list(found.items()):
        if m.rule.startswith("ai") and "/".join(key) not in reviewed:
            del found[key]
            unmatched.append(key)
    return MatchResult(found, unmatched, ambiguous), suggestions, warnings


def hr_data_errors(records: Sequence[HrisRecord]) -> list[Change]:
    """One `flag` per employee id whose data contradicts itself; the problems are joined in
    the reason. A duplicated id lists every record that carries it."""
    out: list[Change] = []
    by_id: dict[str, list[HrisRecord]] = collections.defaultdict(list)
    for r in records:
        by_id[r.employee_id].append(r)
    for eid in sorted(by_id):
        group = by_id[eid]
        problems: list[str] = []
        for r in group:
            if r.end_date and r.start_date and r.end_date < r.start_date:
                problems.append("end date is before the start date")
            if r.status == "terminated" and not r.end_date:
                problems.append("terminated with no end date")
            if r.status == "on_leave" and not r.leave_start:
                problems.append("on leave with no leave start")
        if len(group) > 1:
            emails = ", ".join(sorted(r.work_email for r in group))
            problems.append(f"employee id appears {len(group)} times ({emails})")
        if problems:
            out.append(
                Change(
                    "hr_data_error",
                    eid,
                    eid,
                    "hris",
                    "",
                    "flag",
                    "; ".join(dict.fromkeys(problems)),
                    (f"HRIS record {eid}",),
                )
            )
    return out


class _Builder:
    def __init__(
        self,
        desired: DesiredState,
        actual: ActualState,
        matches: MatchResult,
        policy: Policy,
        records: Sequence[HrisRecord],
        events: Sequence[HrisEvent],
    ) -> None:
        self.desired = desired
        self.actual = actual
        self.matches = matches
        self.policy = policy
        # the first record of an id, in the same order desired_state saw them, so that
        # `people` and `records` always describe the same record
        self.records: dict[str, HrisRecord] = {}
        for r in sorted(records, key=lambda x: x.employee_id):
            self.records.setdefault(r.employee_id, r)
        self.people: dict[str, PersonDesired] = {}
        for p in desired.people:
            if p.category != "superseded":
                self.people.setdefault(p.employee_id, p)
        self.renamed: dict[str, set[str]] = collections.defaultdict(set)  # eid -> first names
        for ev in events:
            if ev.kind == "name_change" and ev.employee_id in self.records:
                rec = self.records[ev.employee_id]
                self.renamed[ev.employee_id] |= {
                    _letters(rec.given_name),
                    _letters(rec.preferred_name),
                } - {""}
        self.worker_names: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        for p in self.people.values():
            rec = self.records.get(p.employee_id)
            if rec is None or p.category not in ("active", "on_leave"):
                continue
            for first in (rec.given_name, rec.preferred_name):
                if _letters(first) and _letters(rec.family_name):
                    self.worker_names[(_letters(first), _letters(rec.family_name))].add(
                        p.employee_id
                    )
        self.changes: dict[tuple[str, str, str, str], Change] = {}
        self.warnings: list[str] = []
        self.moves: dict[str, tuple[str, str]] = {}  # employee -> (date, department)
        self.rehires: dict[str, str] = {}  # employee -> date
        self.terminates: dict[str, tuple[int, str, str]] = {}  # employee -> (day, date, source)
        for ev in sorted(events, key=lambda e: (e.day, e.employee_id)):
            if ev.kind == "terminate":
                self.terminates[ev.employee_id] = (ev.day, ev.date, ev.source)
            elif ev.kind == "move":
                self.moves[ev.employee_id] = (ev.date, dict(ev.fields).get("department", ""))
            elif ev.kind == "rehire":
                self.rehires[ev.employee_id] = ev.date
        self.hand_added: set[tuple[str, str]] = set()
        self.hand_admin: set[tuple[str, str]] = set()
        for e in actual.log:
            if e.via != "admin console" or not e.app:
                continue
            who = (_norm(e.user_login), e.app)
            if e.event_type.endswith("membership.add"):
                self.hand_added.add(who)
            elif e.event_type.endswith("change_privilege"):
                self.hand_admin.add(who)

    # ------------------------------------------------------------------ helpers

    def app_name(self, app_id: str) -> str:
        app = self.policy.apps.get(app_id)
        return app.name if app else app_id

    def add(
        self,
        type_: str,
        employee_id: str,
        a_account: str,
        system: str,
        app_id: str,
        action: str,
        reason: str,
        evidence: Sequence[str] = (),
    ) -> None:
        key = (system, app_id, _norm(a_account), type_)
        if key in self.changes:
            return
        self.changes[key] = Change(
            type_, employee_id, a_account.strip(), system, app_id, action, reason, tuple(evidence)
        )

    # ------------------------------------------------------------------ owned accounts

    def left(self, p: PersonDesired, a: Account, type_: str, suspend_directory: bool) -> None:
        """Leaver / former / contractor-past-end: the same shape, different directory action."""
        record = self.records.get(p.employee_id)
        if type_ == "contractor_past_end":
            end = record.end_date if record else ""
            why = f"contract ended on {end}" if end else "contract ended"
        else:
            end = record.end_date if record else ""
            why = f"left on {end}" if end else "left the company"
        self.directory_or_row(p.employee_id, a, type_, suspend_directory, why)

    def directory_or_row(
        self, eid: str, a: Account, type_: str, suspend_directory: bool, why: str
    ) -> None:
        if a.system == "okta" and not a.app_id:
            if a.state in ("suspended", "deprovisioned"):
                return
            action = "suspend" if suspend_directory else "remove"
        elif a.system == "okta":
            if a.state == "deprovisioned":
                return
            action = "remove"
        elif a.system == "google":
            if a.state == "suspended":
                return
            action = "suspend" if suspend_directory else "remove"
        elif a.system == "slack":
            if a.state == "deprovisioned":
                return
            action = "remove"
        else:
            if a.state == "deprovisioned":
                return
            action = "flag"
        self.add(type_, eid, a.account, a.system, a.app_id, action, why, [f"{a.system} account"])

    def owned(self, p: PersonDesired, a: Account) -> None:
        cat = p.category
        if cat == "leaver":
            self.left(p, a, "leaver_access_left", True)
        elif cat == "former":
            self.left(p, a, "orphaned_account", True)
        elif cat == "contractor_past_end":
            self.left(p, a, "contractor_past_end", False)
        elif cat == "on_leave":
            if a.system in DIRECTORY and not a.app_id and a.state == "active":
                record = self.records.get(p.employee_id)
                since = record.leave_start if record else ""
                self.add(
                    "leave_of_absence",
                    p.employee_id,
                    a.account,
                    a.system,
                    "",
                    "suspend",
                    f"on leave since {since}" if since else "on leave",
                    ["HRIS status on_leave"],
                )
        elif cat == "active":
            self.active(p, a)

    def mover_reason(self, eid: str) -> str:
        date, dept = self.moves[eid]
        return f"moved to {dept} on {date}" if dept else f"moved on {date}"

    def active(self, p: PersonDesired, a: Account) -> None:
        eid = p.employee_id
        if p.unmapped_title:
            return  # no reviewed role: nothing can be judged against it (warned once in run)
        if a.system == "okta" and a.app_id:
            if a.state == "deprovisioned":
                return
            who = (_norm(a.account), a.app_id)
            if a.app_id not in p.sso_apps:
                if who in self.hand_added:
                    self.add(
                        "drift",
                        eid,
                        a.account,
                        "okta",
                        a.app_id,
                        "remove",
                        "granted by hand in the admin console",
                        ["Okta log: membership.add via admin console"],
                    )
                elif eid in self.moves:
                    self.add(
                        "mover_stale_access",
                        eid,
                        a.account,
                        "okta",
                        a.app_id,
                        "remove",
                        self.mover_reason(eid),
                        ["HRIS move event", "app not in the new role"],
                    )
                elif a.scope == "GROUP":
                    # no log entry and no move explain it, and a group assignment is not the
                    # person's own: leave it and say so
                    self.warnings.append(
                        f"{a.account.strip()} ({a.app_id}): group assignment outside role, "
                        "not touched"
                    )
                else:
                    self.add(
                        "drift",
                        eid,
                        a.account,
                        "okta",
                        a.app_id,
                        "flag",
                        "not in the person's role or birthright apps; no log entry or move "
                        "explains it, so a person should look",
                        ["Okta assignment"],
                    )
            elif who in self.hand_admin and a.role == "admin":
                self.add(
                    "drift",
                    eid,
                    a.account,
                    "okta",
                    a.app_id,
                    "flag",
                    "admin role raised by hand in the admin console",
                    ["Okta log: change_privilege via admin console"],
                )
        elif _is_export(a):
            if a.state == "deprovisioned" or a.app_id in p.other_apps:
                return
            if eid in self.moves:
                self.add(
                    "mover_stale_access",
                    eid,
                    a.account,
                    a.system,
                    a.app_id,
                    "flag",
                    self.mover_reason(eid),
                    ["HRIS move event", "export row for an app the new role lacks"],
                )
            elif eid in self.rehires:
                self.add(
                    "rehire",
                    eid,
                    a.account,
                    a.system,
                    a.app_id,
                    "flag",
                    f"rehired on {self.rehires[eid]}; row left from the old role",
                    ["HRIS rehire event", "export row for an app the new role lacks"],
                )
            else:
                self.add(
                    "drift",
                    eid,
                    a.account,
                    a.system,
                    a.app_id,
                    "flag",
                    "export row for an app the person's role does not include",
                    ["export row"],
                )

    # ------------------------------------------------------------------ movers' missing apps

    def export_scheme(self, app_id: str) -> tuple[str | None, set[str], bool]:
        """(template name, usernames in the export, username-only?) from the export's own
        matched rows."""
        rows = [a for a in self.actual.accounts if a.system == app_id and a.app_id == app_id]
        if not rows or any(a.email for a in rows):
            return None, {_norm(a.account) for a in rows}, False
        votes: collections.Counter[str] = collections.Counter()
        for a in rows:
            owner = self.matches.owner(a.key)
            rec = self.records.get(owner) if owner else None
            if not rec:
                continue
            base, _ = _split_number(fold(a.account.strip()))
            for first in {rec.given_name, rec.preferred_name}:
                for name, value in username_templates(first, rec.family_name).items():
                    if value == base:
                        votes[name] += 1
        used = {_norm(a.account) for a in rows}
        if not votes:
            return None, used, True
        best = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return best, used, True

    def missing_apps(self, p: PersonDesired) -> None:
        if p.category != "active" or p.unmapped_title:
            return
        eid = p.employee_id
        have_sso = {
            a.app_id
            for a in self.actual.accounts
            if a.system == "okta" and a.app_id and self.matches.owner(a.key) == eid
        }
        have_rows = {
            a.app_id
            for a in self.actual.accounts
            if _is_export(a) and self.matches.owner(a.key) == eid
        }
        has_okta = any(
            a.system == "okta" and not a.app_id and self.matches.owner(a.key) == eid
            for a in self.actual.accounts
        )
        for app in sorted(p.sso_apps - have_sso):
            if not has_okta:
                continue
            if eid in self.moves:
                self.add(
                    "mover_stale_access",
                    eid,
                    p.email,
                    "okta",
                    app,
                    "add",
                    self.mover_reason(eid),
                    ["HRIS move event", "app is in the new role"],
                )
            else:
                self.warnings.append(
                    f"{p.email} is missing {self.app_name(app)}, a birthright or role app"
                )
        for app in sorted(p.other_apps - have_rows):
            if eid not in self.moves:
                self.warnings.append(
                    f"{p.email} has no row in the {self.app_name(app)} export, "
                    "a birthright or role app"
                )
                continue
            template, used, username_only = self.export_scheme(app)
            account = p.email
            if username_only:
                rec = self.records.get(eid)
                if template and rec:
                    base = username_templates(rec.given_name, rec.family_name)[template]
                    number = _split_number(p.email.split("@")[0].casefold())[1]
                    base = f"{base}{number or ''}"
                    account, n = base, 2
                    while _norm(account) in used:
                        account, n = f"{base}{n}", n + 1
            self.add(
                "mover_stale_access",
                eid,
                account,
                app,
                app,
                "add",
                self.mover_reason(eid),
                ["HRIS move event", "app is in the new role", "no row in the export"],
            )

    # ------------------------------------------------------------------ unowned accounts

    def name_pairs(self, a: Account) -> set[tuple[str, str]]:
        """(first, last) name candidates of an account, letters only and accent-folded."""
        pairs: set[tuple[str, str]] = set()
        if _letters(a.given) and _letters(a.family):
            pairs.add((_letters(a.given), _letters(a.family)))
        words = a.display.split()
        if len(words) >= 2:
            pairs.add((_letters(words[0]), _letters(words[-1])))
        for value in (a.account, a.email):
            local = fold(value.strip().split("@", 1)[0])
            parts = [x for x in re.split(r"[._-]+", local) if x]
            if len(parts) >= 2:
                pairs.add((_letters(parts[0]), _letters(re.sub(r"\d+$", "", parts[-1]))))
        return {p for p in pairs if p[0] and p[1]}

    def looks_renamed(self, a: Account) -> bool:
        """A directory account whose names match exactly one current worker, or whose first
        name is that of a worker with an HRIS name change and whose last name is not the
        worker's current one (the old family name is not in the data)."""
        pairs = self.name_pairs(a)
        for pair in pairs:
            if len(self.worker_names.get(pair, ())) == 1:
                return True
        for eid, firsts in self.renamed.items():
            p = self.people.get(eid)
            rec = self.records.get(eid)
            if p is None or rec is None or p.category not in ("active", "on_leave"):
                continue
            current = _letters(rec.family_name)
            if any(first in firsts and last != current for first, last in pairs):
                return True
        return False

    def orphan(self, a: Account) -> None:
        if not _is_export(a) and self.looks_renamed(a):
            where = a.system
            self.warnings.append(
                f"{a.account.strip()} ({where}): possible renamed person, needs a map entry"
            )
            return
        if _is_export(a):
            if a.state == "deprovisioned":
                return
            self.add(
                "orphaned_account",
                "",
                a.account,
                a.system,
                a.app_id,
                "flag",
                "no matching person in HRIS",
                ["export row"],
            )
            return
        if a.system == "okta" and a.app_id:
            if a.state == "deprovisioned":
                return
            action = "remove"
        elif a.system == "okta":
            if a.state in ("suspended", "deprovisioned"):
                return
            action = "remove"
        elif a.system == "google":
            if a.state == "suspended":
                return
            action = "remove"
        else:
            if a.state == "deprovisioned":
                return
            action = "remove"
        self.add(
            "orphaned_account",
            "",
            a.account,
            a.system,
            a.app_id,
            action,
            "no matching person in HRIS",
            [f"{a.system} account"],
        )

    # ------------------------------------------------------------------ run

    def run(self) -> None:
        bg_seen: dict[tuple[str, str], tuple[str, str]] = {}
        seen: set[tuple[str, str, str]] = set()
        accounts = sorted(
            self.actual.accounts,
            key=lambda a: (a.system, a.app_id, _norm(a.account), a.source_ref),
        )
        ambiguous = set(self.matches.ambiguous)
        unmatched = set(self.matches.unmatched)
        for a in accounts:
            key = a.key
            if key in seen:
                continue
            seen.add(key)
            m = self.matches.matches.get(key)
            if m is not None and m.rule == "break_glass":
                bg_seen.setdefault((a.system, _norm(a.account)), (a.account, m.note))
                continue
            if m is not None:
                p = self.people.get(m.employee_id)
                if p is not None:
                    self.owned(p, a)
                continue
            if key in ambiguous:
                where = f"{a.system}/{a.app_id}" if a.app_id else a.system
                self.warnings.append(
                    f"{a.account.strip()} ({where}): ambiguous owner, needs a map entry"
                )
            elif key in unmatched:
                self.orphan(a)
        for p in sorted(self.people.values(), key=lambda x: x.employee_id):
            self.missing_apps(p)
            if p.category == "active" and p.unmapped_title:
                self.warnings.append(
                    f"unmapped title '{p.title}' for {p.employee_id}: "
                    "needs a reviewed title-map entry"
                )
            if p.notice_until:
                self.warnings.append(
                    f"{p.employee_id} has a termination dated {p.notice_until}, after the "
                    "plan date; treated as active until then, changes held"
                )
        warned: set[str] = set()
        for (system, account_norm), (account, kind) in sorted(bg_seen.items()):
            self.add(
                "break_glass",
                "",
                account,
                system,
                "",
                "never_touch",
                "break-glass account; report only",
                [f"{kind} in the break-glass policy"],
            )
            if kind == "pattern" and account_norm not in warned:
                warned.add(account_norm)
                self.warnings.append(
                    f"{account.strip()} matches the break-glass pattern but isn't listed"
                )


def _break_glass_strings(actual: ActualState, policy: Policy) -> set[str]:
    """Every name a break-glass account goes by (login, email, Google aliases)."""
    names: set[str] = set()
    for a in actual.accounts:
        if _break_glass(policy, a):
            names |= {_norm(v) for v in (a.account, a.email, *a.aliases) if v.strip()}
    return names


def _touches_break_glass(c: Change, policy: Policy, names: set[str]) -> bool:
    account = _norm(c.account)
    if not account:
        return False
    if account in names:
        return True
    if policy.break_glass_kind(account):
        return True
    return "@" not in account and bool(policy.break_glass_kind(f"{account}@{DOMAIN}"))


def build_changes(
    desired: DesiredState,
    actual: ActualState,
    matches: MatchResult,
    policy: Policy,
    *,
    records: Sequence[HrisRecord] = (),
    events: Sequence[HrisEvent] = (),
    map_entries: Iterable[Mapping[str, Any]] = (),
) -> Changes:
    """The changes that bring `actual` to `desired`, deterministic and in a fixed order.

    `records` and `events` are the HRIS data `desired` was built from (names, end dates, the
    move and rehire events). `map_entries` are the account/title map entries; only reviewed
    account entries are used, the rest come back as suggestions.

    When evidence is missing the plan holds or warns; it never removes or suspends."""
    valid_ids = {p.employee_id for p in desired.people if p.category != "superseded"}
    matches, suggestions, map_warnings = apply_reviewed_map(
        matches, map_entries, valid_ids if desired.people else None
    )
    b = _Builder(desired, actual, matches, policy, records, events)
    b.run()
    b.warnings += map_warnings
    changes = list(b.changes.values())
    errors = hr_data_errors(list(records))
    changes += errors
    n = policy.settings.blast_radius_n
    held: list[Change] = []

    def hold(ids: Mapping[str, str]) -> None:
        nonlocal changes
        moved = [c for c in changes if c.employee_id in ids and c.type != "hr_data_error"]
        if not moved:
            return
        held.extend(
            dataclasses.replace(c, reason=f"{c.reason}; held: {ids[c.employee_id]}") for c in moved
        )
        changes = [c for c in changes if not (c.employee_id in ids and c.type != "hr_data_error")]

    # bad HR data, a notice period or a duplicate id: nothing is applied for that person
    reasons: dict[str, str] = {}
    for c in errors:
        reasons[c.employee_id] = f"HR data error ({c.reason})"
    for p in b.people.values():
        if p.notice_until and p.employee_id not in reasons:
            reasons[p.employee_id] = f"notice period until {p.notice_until}"
    hold(reasons)
    # bulk HR changes: a bulk import, or any batch of terminations from one source on one day
    batches: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for eid, (_day, date, source) in sorted(b.terminates.items()):
        person = b.people.get(eid)
        if person is None or eid in reasons:
            continue
        if person.via_bulk_import:
            batches[("bulk_import", "")].append(eid)
        else:
            batches[(date, source)].append(eid)
    for key in sorted(batches):
        ids = set(batches[key])
        caused = sum(1 for c in changes if c.employee_id in ids and c.action in REMOVING)
        if caused <= n or (key[0] != "bulk_import" and len(ids) < MIN_BATCH_PEOPLE):
            continue
        what = (
            f"one bulk import ended {len(ids)} people at once"
            if key[0] == "bulk_import"
            else f"{len(ids)} people were terminated on {key[0]} by {key[1]}"
        )
        why = f"{what}; {caused} removals or suspensions held (limit {n})"
        hold({eid: "bulk HR change awaiting two approvers" for eid in ids})
        changes.append(
            Change(
                "hris_mass_termination",
                "",
                "",
                "hris",
                "",
                "block_two_approvers",
                why,
                ("HRIS terminate events",),
            )
        )
    # last word: nothing but a never_touch entry may name a break-glass account
    names = _break_glass_strings(actual, policy)
    extra_warnings: list[str] = []

    def keep(c: Change) -> bool:
        if c.type == "break_glass" or not _touches_break_glass(c, policy, names):
            return True
        extra_warnings.append(f"change for {c.account.strip()} dropped: break-glass account")
        return False

    changes = [c for c in changes if keep(c)]
    held = [c for c in held if keep(c)]
    changes.sort(key=Change.sort_key)
    held.sort(key=Change.sort_key)
    suggested = sorted(
        (dict(e) for e in suggestions),
        key=lambda e: (str(e.get("kind")), str(e.get("key"))),
    )
    warnings = [*b.warnings, *extra_warnings]
    warnings += [f"title '{t}' has no reviewed title-map entry" for t in desired.unmapped_titles]
    warnings += [f"{eid} needs a decision: {why}" for eid, why in desired.needs_decision]
    warnings += [
        f"policy exception for {eid} on {app} is unused ({why})"
        for eid, app, why in desired.unused_exceptions
    ]
    return Changes(
        tuple(changes), tuple(held), tuple(sorted(dict.fromkeys(warnings))), tuple(suggested)
    )
