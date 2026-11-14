"""Checks that an access world's answer key is consistent with the world (AAC-009).

`check_key(world, recipe)` returns {check name: number of problems}; 0 everywhere means the key
passes. The result holds counts only, never names, accounts or recipe content, so it can be run on
the sealed held-out world by anyone, including the builder, without revealing what's in it.

Four kinds of check:

- **References:** every finding and every owner points to a person and an account that exist in the
  world (an `add` finding may name an account that doesn't exist yet).
- **No duplicates or contradictions:** no finding is listed twice, and no account gets two findings
  with different types or actions.
- **Counts match the recipe:** per problem type, what the key holds equals what the recipe says was
  planted, plus what its custom cases list.
- **Completeness from the world itself:** problems visible in the world's own data (a bulk HR
  import, break-glass accounts, unowned accounts, active accounts of terminated people, people on
  leave with active accounts, grants made by hand in the Okta log, contractors past their end date)
  each have a finding in the key.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable

from meridian_dataset.access.model import AccessFinding, AccessWorld
from meridian_dataset.access.recipe import Recipe

_API_SYSTEMS = ("okta", "google", "slack")
_ADMIN_CONSOLE = "admin console"
_PRIVILEGE = "application.user_membership.change_privilege"

# Recipe count -> the finding type it plants, counted per finding or per distinct person.
_PER_FINDING = {
    "drift": lambda r: r.drift,
    "hris_mass_termination": lambda r: r.mass_termination.events,
    "hr_data_error": lambda r: 0,
}
_PER_PERSON = {
    "mover_stale_access": lambda r: r.movers,
    "leaver_access_left": lambda r: r.leavers_with_access,
    "rehire": lambda r: r.rehires,
    "contractor_past_end": lambda r: r.contractors_past_end,
    "leave_of_absence": lambda r: r.leave_of_absence,
}

CHECKS = (
    "finding_person_missing",
    "finding_account_missing",
    "finding_owner_mismatch",
    "owner_account_missing",
    "account_without_owner",
    "duplicate_findings",
    "contradicting_findings",
    *(
        f"count_mismatch:{t}"
        for t in ("orphaned_account", "break_glass", *_PER_FINDING, *_PER_PERSON)
    ),
    "bulk_import_not_in_key",
    "bulk_import_people_with_removals",
    "break_glass_not_in_key",
    "break_glass_touched",
    "unowned_account_not_in_key",
    "terminated_active_account_not_in_key",
    "on_leave_active_account_not_in_key",
    "hand_grant_not_in_key",
    "past_end_contractor_not_in_key",
    "orphan_app_access_not_in_key",
    "break_glass_system_not_in_key",
    "privilege_drift_on_unassigned_app",
)


def _norm(value: str) -> str:
    return value.strip().lower()


class _Accounts:
    """Every account in the world, per system, by normalised name."""

    def __init__(self, world: AccessWorld, recipe: Recipe) -> None:
        self.okta_by_name: dict[str, str] = {}  # login or email -> okta user id
        self.okta_active: set[str] = set()
        for u in world.okta.users:
            for name in (u.login, u.email):
                self.okta_by_name[_norm(name)] = u.id
            if u.status == "ACTIVE":
                self.okta_active.add(u.id)
        slug_by_okta_id = {a.id: a.app_id for a in world.okta.apps}
        self.okta_slug = {a.app_id: a.id for a in world.okta.apps}
        self.assigned = {
            (slug_by_okta_id.get(a.app_id, ""), a.user_id) for a in world.okta.assignments
        }
        self.google: dict[str, bool] = {}  # name -> active
        for g in world.google_users:
            for name in (g.primary_email, *g.aliases):
                self.google[_norm(name)] = not g.suspended
        self.slack: dict[str, bool] = {}
        for s in world.slack_users:
            for name in (s.user_name, s.email):
                self.slack[_norm(name)] = s.active
        fields = {
            layout.name: [c.field for c in layout.columns] for layout in recipe.export_layouts
        }
        self.export_rows: dict[str, set[str]] = defaultdict(set)  # app_id -> identifier cells
        for export in world.exports:
            cols = fields.get(export.layout, [])
            ids = [i for i, f in enumerate(cols) if f in ("email", "username")]
            for row in export.rows:
                for i in ids:
                    if i < len(row) and row[i].strip():
                        self.export_rows[export.app_id].add(_norm(row[i]))

    def exists(self, system: str, app_id: str, account: str) -> bool:
        name = _norm(account)
        if system == "okta":
            user = self.okta_by_name.get(name)
            if user is None:
                return False
            return not app_id or (app_id, user) in self.assigned
        if system == "google":
            return name in self.google
        if system == "slack":
            return name in self.slack
        return name in self.export_rows.get(system, set())

    def active(self, system: str, account: str) -> bool:
        name = _norm(account)
        if system == "okta":
            return self.okta_by_name.get(name) in self.okta_active
        if system == "google":
            return self.google.get(name, False)
        if system == "slack":
            return self.slack.get(name, False)
        return name in self.export_rows.get(system, set())

    def every_account(self, world: AccessWorld) -> Iterable[tuple[str, str]]:
        for u in world.okta.users:
            yield "okta", _norm(u.login)
        for g in world.google_users:
            yield "google", _norm(g.primary_email)
        for s in world.slack_users:
            yield "slack", _norm(s.user_name)
        for app_id, names in self.export_rows.items():
            for name in names:
                yield app_id, name


def _case_counts(recipe: Recipe) -> tuple[Counter[str], Counter[str]]:
    """Per finding type: findings listed by custom cases, and distinct case people with one."""
    findings: Counter[str] = Counter()
    people: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for case in recipe.custom_cases:
        for f in case.findings:
            findings[f.type] += 1
            if f.ref is not None:
                people[f.type].add((case.id, f.ref))
    return findings, Counter({t: len(p) for t, p in people.items()})


def _is_app_access(f: AccessFinding) -> bool:
    """An SSO app-access finding: system okta with an app id."""
    return f.system == "okta" and bool(f.app_id)


def _touches(f: AccessFinding, system: str, name: str) -> bool:
    return f.system == system and _norm(f.account) == name


def check_key(world: AccessWorld, recipe: Recipe) -> dict[str, int]:
    """{check name: problems}. Counts only; 0 everywhere means the key is consistent."""
    key = world.answer_key
    acc = _Accounts(world, recipe)
    problems: Counter[str] = Counter({name: 0 for name in CHECKS})
    hris = {r.employee_id: r for r in world.hris.records}
    owners = {(o.system, _norm(o.account)): o.owner_employee_id for o in key.owners}
    findings = key.findings

    # References.
    for f in findings:
        if f.employee_id and f.employee_id not in hris:
            problems["finding_person_missing"] += 1
        if f.system == "hris" or f.expected_action == "add" or not f.account:
            continue
        if not acc.exists(f.system, f.app_id if f.system == "okta" else "", f.account):
            problems["finding_account_missing"] += 1
        owner = owners.get((f.system, _norm(f.account)))
        if f.employee_id and owner is not None and owner != f.employee_id:
            problems["finding_owner_mismatch"] += 1
    for o in key.owners:
        if not acc.exists(o.system, "", o.account):
            problems["owner_account_missing"] += 1
    for system, name in acc.every_account(world):
        if (system, name) not in owners:
            problems["account_without_owner"] += 1

    # Duplicates and contradictions.
    seen = Counter((f.type, f.employee_id, _norm(f.account), f.system, f.app_id) for f in findings)
    problems["duplicate_findings"] = sum(n - 1 for n in seen.values() if n > 1)
    by_target: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for f in findings:
        if f.system == "hris":
            continue
        target = (f.system, f.app_id, _norm(f.account) or f.employee_id)
        by_target[target].add((f.type, f.expected_action))
    problems["contradicting_findings"] = sum(1 for v in by_target.values() if len(v) > 1)

    # Counts match the recipe (plus its custom cases).
    case_findings, case_people = _case_counts(recipe)
    per_type = Counter(f.type for f in findings)
    for t, planted in _PER_FINDING.items():
        if per_type[t] != planted(recipe) + case_findings[t]:
            problems[f"count_mismatch:{t}"] += 1
    # Orphaned accounts count accounts, not the app-access findings of an orphaned Okta user.
    accounts = {
        (f.system, _norm(f.account))
        for f in findings
        if f.type == "orphaned_account" and not _is_app_access(f)
    }
    case_accounts = sum(
        1
        for case in recipe.custom_cases
        for f in case.findings
        if f.type == "orphaned_account" and not _is_app_access(f)
    )
    if len(accounts) != recipe.orphaned_accounts + case_accounts:
        problems["count_mismatch:orphaned_account"] += 1
    # Break-glass counts identities: one login may have a finding in several systems.
    glass_ids = {_norm(f.account) for f in findings if f.type == "break_glass"}
    case_glass = {
        _norm(f.account)
        for case in recipe.custom_cases
        for f in case.findings
        if f.type == "break_glass"
    }
    if len(glass_ids) != recipe.break_glass + len(case_glass):
        problems["count_mismatch:break_glass"] += 1
    for t, planted in _PER_PERSON.items():
        distinct = len({f.employee_id for f in findings if f.type == t and f.employee_id})
        if distinct != planted(recipe) + case_people[t]:
            problems[f"count_mismatch:{t}"] += 1

    # Completeness, from the world's own data.
    bulk = {e.employee_id for e in world.hris.events if e.source == "bulk_import"}
    if bulk and per_type["hris_mass_termination"] == 0:
        problems["bulk_import_not_in_key"] += 1
    problems["bulk_import_people_with_removals"] = sum(
        1 for f in findings if f.employee_id in bulk and f.expected_action in ("remove", "suspend")
    )
    glass = {_norm(a) for a in key.break_glass_accounts}
    glass_findings = {_norm(f.account) for f in findings if f.type == "break_glass"}
    problems["break_glass_not_in_key"] = len(glass - glass_findings)
    problems["break_glass_touched"] = sum(
        1 for f in findings if f.type != "break_glass" and _norm(f.account) in glass
    )
    glass_systems = {(f.system, _norm(f.account)) for f in findings if f.type == "break_glass"}
    for system, name in acc.every_account(world):
        if system in ("okta", "google") and name in glass and (system, name) not in glass_systems:
            problems["break_glass_system_not_in_key"] += 1
    flagged = {(f.system, _norm(f.account)) for f in findings if f.account}
    for (system, name), owner in owners.items():
        if owner or name in glass or (system, name) in flagged:
            continue
        problems["unowned_account_not_in_key"] += 1
    reference = world.reference_date
    has_finding = {f.employee_id for f in findings if f.employee_id}
    for (system, name), owner in owners.items():
        person = hris.get(owner)
        if person is None or owner in bulk or not acc.active(system, name):
            continue
        ended = person.end_date == "" or person.end_date <= reference
        if person.status == "terminated" and ended and (system, name) not in flagged:
            problems["terminated_active_account_not_in_key"] += 1
        if person.status == "on_leave" and system in _API_SYSTEMS and owner not in has_finding:
            problems["on_leave_active_account_not_in_key"] += 1
        past_end = person.end_date != "" and person.end_date < reference
        if (
            person.employment_type == "contractor"
            and person.status == "active"
            and past_end
            and owner not in has_finding
        ):
            problems["past_end_contractor_not_in_key"] += 1
    slug_by_okta_id = {a.id: a.app_id for a in world.okta.apps}
    orphan_users = {
        acc.okta_by_name[_norm(f.account)]
        for f in findings
        if f.type == "orphaned_account"
        and f.system == "okta"
        and not f.app_id
        and _norm(f.account) in acc.okta_by_name
    }
    user_by_id = {u.id: _norm(u.login) for u in world.okta.users}
    orphan_app = {
        (f.app_id, _norm(f.account))
        for f in findings
        if f.type == "orphaned_account" and _is_app_access(f)
    }
    for a in world.okta.assignments:
        if a.user_id in orphan_users:
            slug = slug_by_okta_id.get(a.app_id, "")
            if (slug, user_by_id[a.user_id]) not in orphan_app:
                problems["orphan_app_access_not_in_key"] += 1
    admins = {
        (slug_by_okta_id.get(a.app_id, ""), a.user_id)
        for a in world.okta.assignments
        if a.role == "admin"
    }
    for event in world.okta.log:
        if event.event_type == _PRIVILEGE and event.via == _ADMIN_CONSOLE:
            slug = slug_by_okta_id.get(event.target_app_id, "")
            if (slug, event.target_user_id) not in admins:
                problems["privilege_drift_on_unassigned_app"] += 1
    for event in world.okta.log:
        if event.via != _ADMIN_CONSOLE:
            continue
        slug = slug_by_okta_id.get(event.target_app_id, "")
        login = _norm(event.target_user_login)
        if not any(
            f.system == "okta" and f.app_id == slug and _norm(f.account) == login for f in findings
        ):
            problems["hand_grant_not_in_key"] += 1
    return dict(problems)


def total(problems: dict[str, int]) -> int:
    return sum(problems.values())
