"""Build the access world from a seed and a recipe (AAC-004, AAC-005).

One `random.Random(seed)` is drawn from in a fixed order, so the same seed and recipe give
the same bytes. The order is the order of the phases in `_Builder.build`:

1. slots: 230 current workers (department, role, country, contractors, managers)
2. select: who gets which planted problem
3. extras: leavers, former employees, rehires' old records
4. names and emails, then titles, dates and statuses
5. access: what each person has in Okta and in the non-SSO exports
6. accounts: Okta, Google, Slack, the Okta log, break-glass and unowned accounts
7. exports: the 95 messy CSV files

The answer key (true roles, account owners, findings) is built alongside and kept apart.
"""

import random
import string
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from meridian_dataset.access import catalog as cat
from meridian_dataset.access.model import (
    ACCESS_GENERATOR_VERSION,
    ACCESS_TUNING_SEED,
    DOMAIN,
    MONTH_START,
    PERSONAL_DOMAIN,
    REFERENCE_DATE,
    AccessAnswerKey,
    AccessFinding,
    AccessWorld,
    AccountOwner,
    AppExport,
    GoogleUser,
    Hris,
    HrisEvent,
    HrisRecord,
    Okta,
    OktaApp,
    OktaAssignment,
    OktaLogEvent,
    OktaUser,
    SlackScimUser,
)
from meridian_dataset.access.recipe import Recipe, load_recipe, recipe_digest, validate_recipe
from meridian_dataset.names import FAMILY_NAMES, GIVEN_NAMES

_MONTH = date.fromisoformat(MONTH_START)
_REF = date.fromisoformat(REFERENCE_DATE)
_ALNUM = string.ascii_letters + string.digits
_ALNUM_UPPER = string.ascii_uppercase + string.digits
_PROVISIONER = "aac-provisioner@meridian.example"
_BIRTHRIGHT_RULE = "group-rule-birthright"
_ADD = "application.user_membership.add"
_PRIVILEGE = "application.user_membership.change_privilege"
_NO_ACCESS_KINDS = ("clean_former", "rehire_old")
_FORMER_KINDS = ("leaver", "orphan_former", "clean_former", "rehire_old")

Template = tuple[str, str, int, str]  # (finding type, expected action, day, detail)


def _day(n: int) -> date:
    return _MONTH + timedelta(days=n)


def _req_p(p: "_P | None") -> "_P":
    if p is None:
        raise ValueError("a twin the builder should have set is missing")
    return p


def _req(value: date | None) -> date:
    if value is None:
        raise ValueError("a date the builder should have set is missing")
    return value


def _local(email: str) -> str:
    return email.split("@", 1)[0]


def _fmt_date(d: date, style: str) -> str:
    if style == "iso":
        return d.isoformat()
    if style == "dmy_slash":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "mdy_slash":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    return f"{d.day} {cat.MONTH_ABBR[d.month - 1]} {d.year}"


@dataclass(eq=False)
class _P:
    """A person while the world is being built (mutable on purpose)."""

    eid: str
    order: int
    dept: str
    role: str
    country: str
    contractor: bool = False
    is_manager: bool = False
    manager_id: str = ""
    kind: str = "regular"
    accent: bool = False
    twin: "_P | None" = None
    old_role: str = ""
    day: int = 0
    given: str = ""
    family: str = ""
    preferred: str = ""
    email: str = ""
    personal: str = ""
    old_email: str = ""
    old_family: str = ""
    title: str = ""
    status: str = "active"
    start: date = _MONTH
    end: date | None = None
    leave_start: date | None = None
    leave_end: date | None = None
    sso: dict[str, str] = field(default_factory=dict)
    sso_created: dict[str, date] = field(default_factory=dict)
    exports: list[str] = field(default_factory=list)
    stale_sso: list[str] = field(default_factory=list)
    missing_sso: list[str] = field(default_factory=list)
    missing_other: list[str] = field(default_factory=list)
    drift: list[tuple[str, str, int]] = field(default_factory=list)  # app, role, day
    old_email_apps: set[str] = field(default_factory=set)
    row_findings: dict[str, Template] = field(default_factory=dict)
    orphan_where: str = ""
    okta: str = ""  # "" | ACTIVE | DEPROVISIONED
    google: str = ""  # "" | active | suspended
    slack: str = ""  # "" | active | inactive
    login: date = _MONTH
    login_ts: str = ""

    @property
    def display(self) -> str:
        return f"{self.preferred or self.given} {self.family}"


@dataclass(eq=False)
class _Entry:
    """One row waiting to be written into a non-SSO app's export."""

    app: str
    owner: str
    given: str
    family: str
    display: str
    email: str
    dept: str
    when: date
    order: int
    username: str = ""  # for accounts that match nobody
    templates: list[Template] = field(default_factory=list)


def _dept_sizes(headcount: int) -> dict[str, int]:
    rest = headcount - cat.IT_SIZE
    sizes = {d: rest * w // 100 for d, w in cat.DEPT_WEIGHTS.items()}
    sizes["Engineering"] += rest - sum(sizes.values())
    return {d: sizes.get(d, cat.IT_SIZE) for d in cat.DEPT_NAMES}


def _role_apps(role_id: str, contractor: bool) -> tuple[list[str], list[str]]:
    role = cat.ROLE_BY_ID[role_id]
    birthright = cat.BIRTHRIGHT_IDS[:-2] if contractor else cat.BIRTHRIGHT_IDS
    return [*birthright, *role.sso_apps], list(role.other_apps)


def _username(scheme: str, given: str, family: str, email: str) -> str:
    local = _local(email)
    if scheme == "local":
        return local
    first = cat.ascii_fold(given).lower()
    last = cat.ascii_fold(family).lower().replace(" ", "")
    digits = local[len(local.rstrip(string.digits)) :]
    base = first[0] + last if scheme == "initial_last" else f"{first}.{last[0]}"
    return base + digits


class _Builder:
    def __init__(self, seed: int, recipe: Recipe) -> None:
        self.seed = seed
        self.rc = recipe
        self.rng = random.Random(seed)
        self.people: list[_P] = []
        self.eids: set[str] = set()
        self.ids: set[str] = set()
        self.by_dept: dict[str, list[_P]] = {d: [] for d in cat.DEPT_NAMES}
        self.managers: dict[str, list[_P]] = {}
        self.findings: list[AccessFinding] = []
        self.owners: list[AccountOwner] = []
        self.events: list[HrisEvent] = []
        self.entries: list[_Entry] = []
        self.assign: list[tuple[str, str, str, str, str]] = []
        self.log: list[OktaLogEvent] = []
        self.okta_users: list[OktaUser] = []
        self.google_users: list[GoogleUser] = []
        self.slack_users: list[SlackScimUser] = []
        self.break_glass: list[str] = []
        self.okta_app_ids: dict[str, str] = {}

    # ---- small helpers ----------------------------------------------------------------

    def _eid(self) -> str:
        while True:
            eid = f"E{self.rng.randrange(10000, 100000)}"
            if eid not in self.eids:
                self.eids.add(eid)
                return eid

    def _oid(self, prefix: str, length: int = 17, alphabet: str = _ALNUM) -> str:
        while True:
            value = prefix + "".join(self.rng.choices(alphabet, k=length))
            if value not in self.ids:
                self.ids.add(value)
                return value

    def _ts(self, d: date) -> str:
        secs = self.rng.randrange(7 * 3600, 19 * 3600)
        return f"{d.isoformat()}T{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}.000Z"

    def _find(
        self,
        kind: str,
        owner: str,
        account: str,
        system: str,
        app: str,
        action: str,
        day: int,
        detail: str,
    ) -> None:
        self.findings.append(AccessFinding(kind, owner, account, system, app, action, day, detail))

    def _owner(self, system: str, account: str, owner: str) -> None:
        self.owners.append(AccountOwner(system, account, owner))

    # ---- build ------------------------------------------------------------------------

    def build(self) -> AccessWorld:
        self._slots()
        self._select()
        self._extras()
        self._names()
        self._profiles()
        self._access()
        self._accounts()
        self._ghosts()
        exports = self._exports()
        records, events = self._hris()
        key = AccessAnswerKey(
            seed=self.seed,
            generator_version=ACCESS_GENERATOR_VERSION,
            recipe_name=self.rc.name,
            recipe_sha256=recipe_digest(self.rc),
            true_roles=tuple(
                sorted((p.eid, p.role) for p in self.people if p.kind not in _FORMER_KINDS)
            ),
            owners=tuple(sorted(self.owners, key=lambda o: (o.system, o.account))),
            findings=tuple(
                sorted(
                    self.findings,
                    key=lambda f: (
                        f.type,
                        f.day,
                        f.employee_id,
                        f.system,
                        f.app_id,
                        f.account,
                        f.expected_action,
                    ),
                )
            ),
            break_glass_accounts=tuple(self.break_glass),
        )
        users = sorted(self.okta_users, key=lambda u: u.login)
        by_user = {u.id: u.login for u in users}
        okta = Okta(
            users=tuple(users),
            apps=tuple(
                OktaApp(self.okta_app_ids[a.app_id], a.name, a.app_id) for a in cat.APPS if a.sso
            ),
            assignments=tuple(
                OktaAssignment(app_id, user_id, created, scope, role)
                for app_id, user_id, created, scope, role in sorted(
                    self.assign, key=lambda t: (by_user[t[1]], t[0])
                )
            ),
            log=tuple(sorted(self.log, key=lambda e: (e.published, e.uuid))),
        )
        return AccessWorld(
            seed=self.seed,
            generator_version=ACCESS_GENERATOR_VERSION,
            recipe_name=self.rc.name,
            reference_date=REFERENCE_DATE,
            month_start=MONTH_START,
            domain=DOMAIN,
            hris=Hris(records, events),
            okta=okta,
            google_users=tuple(sorted(self.google_users, key=lambda g: g.primary_email)),
            slack_users=tuple(sorted(self.slack_users, key=lambda s: s.user_name)),
            exports=exports,
            answer_key=key,
        )

    # ---- 1. slots ---------------------------------------------------------------------

    def _slots(self) -> None:
        r = self.rng
        sizes = _dept_sizes(self.rc.headcount)
        for dept in cat.DEPT_NAMES:
            n = sizes[dept]
            if dept == "IT":
                roles = ["it-lead", "it-engineer", "it-engineer"]
                countries = ["DE", "DE", "CA"]
            else:
                dept_roles = cat.ROLES_BY_DEPT[dept]
                roles = [x.role_id for x in dept_roles for _ in range(2)]
                if n < len(roles):
                    raise ValueError(f"{dept} is too small for its roles")
                roles += r.choices(
                    [x.role_id for x in dept_roles],
                    weights=[x.weight for x in dept_roles],
                    k=n - len(roles),
                )
                r.shuffle(roles)
                countries = r.choices(cat.COUNTRIES, weights=cat.COUNTRY_WEIGHTS, k=n)
            for role, country in zip(roles, countries, strict=True):
                p = _P(self._eid(), len(self.people), dept, role, country)
                self.people.append(p)
                self.by_dept[dept].append(p)
        for i in range(self.rc.contractors):
            dept = cat.CONTRACTOR_DEPTS[i % len(cat.CONTRACTOR_DEPTS)]
            r.choice([p for p in self.by_dept[dept] if not p.contractor]).contractor = True
        for dept, members in self.by_dept.items():
            staff = [p for p in members if not p.contractor]
            k = 1 if dept == "IT" else max(1, len(members) // 8)
            managers = staff[:k]
            self.managers[dept] = managers
            for p in members:
                p.is_manager = p in managers
                if p is managers[0]:
                    p.manager_id = ""
                elif p.is_manager:
                    p.manager_id = managers[0].eid
                else:
                    p.manager_id = r.choice(managers).eid

    # ---- 2. select --------------------------------------------------------------------

    def _select(self) -> None:
        r, rc = self.rng, self.rc
        mass = rc.mass_termination
        for event in range(mass.events):
            candidates = [
                p
                for p in self.by_dept[mass.department]
                if p.kind == "regular" and not p.contractor and not p.is_manager
            ]
            if len(candidates) < mass.people:
                raise ValueError(f"{mass.department} has too few people for the mass termination")
            for p in r.sample(candidates, mass.people):
                p.kind, p.day = "mass", mass.day - event
        contractors = [p for p in self.people if p.contractor]
        for p in r.sample(contractors, rc.contractors_past_end):
            p.kind = "past_end"
        pool = [
            p
            for p in self.people
            if p.kind == "regular" and p.dept != "IT" and not p.contractor and not p.is_manager
        ]
        r.shuffle(pool)

        def take(n: int, kind: str) -> list[_P]:
            if n > len(pool):
                raise ValueError(f"not enough people for {kind}")
            got = pool[:n]
            del pool[:n]
            for p in got:
                p.kind = kind
            return got

        d_roles = self._roles_with_username_export()
        for _ in range(rc.duplicate_name_pairs):
            by_role: dict[str, list[_P]] = {}
            for p in pool:
                by_role.setdefault(p.role, []).append(p)
            options = [x for x in sorted(by_role) if x in d_roles and len(by_role[x]) >= 2]
            if not options:
                raise ValueError("no role can host a duplicate-name pair")
            first, second = sorted(by_role[r.choice(options)][:2], key=lambda p: p.order)
            for p in (first, second):
                pool.remove(p)
                p.kind = "dup"
            second.twin = first
        for p in take(rc.movers, "mover"):
            p.day = r.randint(2, 26)
            options = [
                x.role_id
                for x in cat.ROLES
                if x.department not in (p.dept, "IT")
                and set(x.sso_apps) - set(cat.ROLE_BY_ID[p.role].sso_apps)
                and set(x.other_apps) - set(cat.ROLE_BY_ID[p.role].other_apps)
            ]
            p.old_role = r.choice(options)
        for p in take(rc.leave_of_absence, "leave"):
            p.day = r.randint(2, 24)
        for p in take(rc.name_changes, "namechg"):
            p.day = r.randint(2, 26)
        take(rc.drift, "drift")
        for p in take(rc.rehires, "rehire_new"):
            p.day = r.randint(1, 26)
            new_other = set(cat.ROLE_BY_ID[p.role].other_apps)
            options = [
                x.role_id
                for x in cat.ROLES
                if x.department != "IT" and x.role_id != p.role and set(x.other_apps) - new_other
            ]
            p.old_role = r.choice(options)
        for p in take(cat.HIRES_IN_MONTH, "hire"):
            p.day = r.randint(2, 27)
        seen: set[str] = set()
        for p in pool:
            if len(seen) == cat.ACCENTED_PEOPLE:
                break
            if p.role not in seen:
                seen.add(p.role)
                p.accent = True

    def _layout_plan(self) -> dict[str, dict[str, str]]:
        """Per non-SSO app: layout, date format, username scheme. Pure function of the index."""
        rc = self.rc
        n = cat.NON_SSO_COUNT
        d_idx = {i * n // rc.username_only_exports for i in range(rc.username_only_exports)}
        letters = "ABC"[: min(3, rc.export_layouts)]
        plan: dict[str, dict[str, str]] = {}
        plain = d_count = 0
        for j, app in enumerate(cat.NON_SSO_IDS):
            if j in d_idx:
                layout = "D"
                scheme = ("initial_last", "first_l", "local")[d_count % 3]
                d_count += 1
            else:
                layout, scheme = letters[plain % len(letters)], ""
                plain += 1
            plan[app] = {
                "layout": layout,
                "scheme": scheme,
                "format": rc.date_formats[j % len(rc.date_formats)],
            }
        return plan

    def _roles_with_username_export(self) -> set[str]:
        plan = self._layout_plan()
        return {x.role_id for x in cat.ROLES if any(plan[a]["layout"] == "D" for a in x.other_apps)}

    # ---- 3. extras --------------------------------------------------------------------

    def _extra(self, kind: str, dept: str | None = None, role: str | None = None) -> _P:
        r = self.rng
        if role is None:
            if dept is None:
                dept = r.choices(list(cat.DEPT_WEIGHTS), weights=list(cat.DEPT_WEIGHTS.values()))[0]
            roles = cat.ROLES_BY_DEPT[dept]
            role = r.choices([x.role_id for x in roles], weights=[x.weight for x in roles])[0]
        dept = cat.ROLE_BY_ID[role].department
        country = r.choices(cat.COUNTRIES, weights=cat.COUNTRY_WEIGHTS)[0]
        p = _P(self._eid(), len(self.people), dept, role, country, kind=kind)
        p.manager_id = r.choice(self.managers[dept]).eid
        self.people.append(p)
        return p

    def _extras(self) -> None:
        rc = self.rc
        for _ in range(rc.leavers_with_access):
            self._extra("leaver")
        for i in range(rc.orphaned_former_employees):
            self._extra("orphan_former").orphan_where = "okta" if i % 2 == 0 else "export"
        for _ in range(cat.CLEAN_FORMER):
            self._extra("clean_former")
        for p in [p for p in self.people if p.kind == "rehire_new"]:
            old = self._extra("rehire_old", role=p.old_role)
            old.twin = p

    # ---- 4. names, emails, titles, dates ----------------------------------------------

    def _names(self) -> None:
        r = self.rng
        plain = [(g, f) for g in GIVEN_NAMES for f in FAMILY_NAMES]
        accented = [(g, f) for g in GIVEN_NAMES for f in cat.EXTRA_FAMILY_NAMES]
        r.shuffle(plain)
        r.shuffle(accented)
        used: set[tuple[str, str]] = set()
        counts: dict[str, int] = {}
        for p in self.people:
            if p.twin is not None:
                p.given, p.family = p.twin.given, p.twin.family
            else:
                p.given, p.family = (accented if p.accent else plain).pop()
            used.add((p.given, p.family))
            if p.kind == "rehire_old":
                p.email = _req_p(p.twin).email  # same address re-issued
            else:
                p.email = self._email(p, counts)
            if p.given in cat.PREFERRED_NAMES and r.random() < 0.5:
                p.preferred = cat.PREFERRED_NAMES[p.given]
        for p in self.people:
            if p.kind == "namechg":
                p.old_email, p.old_family = p.email, p.family
                while True:
                    family = r.choice(FAMILY_NAMES)
                    if (p.given, family) not in used and family != p.family:
                        break
                p.family = family
                used.add((p.given, family))
                p.email = self._email(p, counts)

    @staticmethod
    def _email(p: _P, counts: dict[str, int]) -> str:
        base = f"{cat.ascii_fold(p.given).lower()}.{cat.ascii_fold(p.family).lower()}"
        n = counts.get(base, 0)
        counts[base] = n + 1
        return f"{base if n == 0 else f'{base}{n + 1}'}@{DOMAIN}"

    def _profiles(self) -> None:
        r = self.rng
        for p in self.people:
            role = cat.ROLE_BY_ID[p.role]
            p.title = r.choice(role.variants)
            p.personal = (
                f"{cat.ascii_fold(p.given).lower()}.{cat.ascii_fold(p.family).lower()}"
                f"{r.randrange(10, 100)}@{PERSONAL_DOMAIN}"
            )
            k = p.kind
            if k in ("hire", "rehire_new"):
                p.start = _day(p.day)
            elif k == "leaver":
                p.day = r.randint(1, 27)
                p.end = _day(p.day)
                p.start = p.end - timedelta(days=r.randint(200, 3000))
                p.status = "terminated"
            elif k in ("orphan_former", "clean_former"):
                p.end = date(2026, 1, 5) + timedelta(days=r.randint(0, 290))
                p.start = p.end - timedelta(days=r.randint(200, 3000))
                p.status = "terminated"
            elif k == "rehire_old":
                p.end = date(2026, 2, 1) + timedelta(days=r.randint(0, 240))
                p.start = p.end - timedelta(days=r.randint(200, 3000))
                p.status = "terminated"
            elif k == "mass":
                p.start = _REF - timedelta(days=r.randint(90, 3300))
                p.end = _day(p.day)
                p.status = "terminated"
            elif k == "past_end":
                p.start = _REF - timedelta(days=r.randint(200, 700))
                p.end = _REF - timedelta(days=r.randint(16, 122))
            elif p.contractor:
                p.start = _REF - timedelta(days=r.randint(40, 500))
                p.end = date(2027, 1, 31) + timedelta(days=r.randint(0, 150))
            else:
                p.start = _REF - timedelta(days=r.randint(90, 3300))
            if k == "leave":
                p.status = "on_leave"
                p.leave_start = _day(p.day)
                p.leave_end = _REF + timedelta(days=r.randint(15, 60))

    # ---- 5. access --------------------------------------------------------------------

    def _access(self) -> None:
        r = self.rng
        for p in self.people:
            k = p.kind
            if k in _NO_ACCESS_KINDS:
                continue
            role = cat.ROLE_BY_ID[p.role]
            sso, other = _role_apps(p.role, p.contractor)
            if k == "orphan_former":
                if p.orphan_where == "okta":
                    sso, other = [r.choice([*cat.BIRTHRIGHT_IDS, *role.sso_apps])], []
                else:
                    sso, other = [], [r.choice(role.other_apps)]
                    p.row_findings[other[0]] = (
                        "orphaned_account",
                        "flag",
                        0,
                        f"{p.display} left before November; still has a row in this export.",
                    )
            elif k == "leaver":
                other = r.sample(other, min(len(other), r.randint(1, 3)))
                for app in other:
                    p.row_findings[app] = (
                        "leaver_access_left",
                        "flag",
                        p.day,
                        f"{p.display} left on {p.end}; still listed in this app's export.",
                    )
            elif k == "past_end":
                for app in other:
                    p.row_findings[app] = (
                        "contractor_past_end",
                        "flag",
                        self._end_day(p),
                        f"Contractor {p.display}'s end date {p.end} has passed; still listed.",
                    )
            elif k == "mover":
                sso, other = self._mover_access(p)
            elif k == "rehire_new":
                old = cat.ROLE_BY_ID[p.old_role]
                stale = [a for a in old.other_apps if a not in role.other_apps]
                for app in r.sample(stale, min(len(stale), r.randint(1, 2))):
                    other.append(app)
                    p.row_findings[app] = (
                        "rehire",
                        "flag",
                        p.day,
                        f"{p.display} was rehired in a new role; row left from the old role.",
                    )
            p.sso = {a: "member" for a in sso}
            p.sso_created = {a: p.start for a in sso}
            if k == "mover":
                for a in p.sso:
                    if a in role.sso_apps and a not in cat.ROLE_BY_ID[p.old_role].sso_apps:
                        p.sso_created[a] = _day(p.day)
            p.exports = other
            if k == "namechg" and p.exports:
                p.old_email_apps = set(r.sample(p.exports, r.randint(1, len(p.exports))))
        self._plan_drift()

    def _plan_drift(self) -> None:
        r = self.rng
        drifters = [p for p in self.people if p.kind == "drift"]
        admins = min(len(drifters), max(2, len(drifters) // 3))
        for i, p in enumerate(drifters):
            wants_admin = i < admins
            options = [
                a
                for a in cat.SSO_IDS[cat.BIRTHRIGHT_COUNT :]
                if a not in p.sso and (cat.APP_BY_ID[a].has_admin_role or not wants_admin)
            ]
            app = r.choice(options)
            day = r.randint(2, 27)
            p.drift.append((app, "admin" if wants_admin else "member", day))
            p.sso[app] = "admin" if wants_admin else "member"
            p.sso_created[app] = _day(day)

    def _mover_access(self, p: _P) -> tuple[list[str], list[str]]:
        r = self.rng
        role, old = cat.ROLE_BY_ID[p.role], cat.ROLE_BY_ID[p.old_role]
        birthright = list(cat.BIRTHRIGHT_IDS)
        new_only = [("sso", a) for a in role.sso_apps if a not in old.sso_apps]
        new_only += [("other", a) for a in role.other_apps if a not in old.other_apps]
        missing = r.sample(new_only, max(1, len(new_only) // 2))
        p.missing_sso = [a for kind, a in missing if kind == "sso"]
        p.missing_other = [a for kind, a in missing if kind == "other"]
        p.stale_sso = [a for a in old.sso_apps if a not in role.sso_apps]
        stale_other = [a for a in old.other_apps if a not in role.other_apps]
        for app in stale_other:
            p.row_findings[app] = (
                "mover_stale_access",
                "flag",
                p.day,
                f"{p.display} moved to {p.dept} on {_day(p.day)}; old role's app still listed.",
            )
        sso = birthright + p.stale_sso
        sso += [a for a in role.sso_apps if a not in p.missing_sso]
        other = list(old.other_apps)
        other += [a for a in role.other_apps if a not in p.missing_other and a not in other]
        return sso, other

    @staticmethod
    def _end_day(p: _P) -> int:
        end = _req(p.end)
        return (end - _MONTH).days if end >= _MONTH else 0

    # ---- 6. accounts ------------------------------------------------------------------

    def _login_date(self, p: _P) -> date:
        r = self.rng
        if p.kind == "mass":  # still logging in after the bulk termination
            return date(2026, 11, 30) + timedelta(days=r.randint(0, 1))
        if p.kind in ("leaver", "orphan_former", "clean_former"):
            return _req(p.end) - timedelta(days=r.randint(0, 3))
        if p.status == "on_leave":
            return _req(p.leave_start) - timedelta(days=r.randint(1, 5))
        if p.kind in ("hire", "rehire_new"):
            return min(p.start + timedelta(days=r.randint(0, 3)), _REF - timedelta(days=1))
        return _REF - timedelta(days=r.randint(1, 14))

    def _accounts(self) -> None:
        r = self.rng
        for app in cat.APPS:
            if app.sso:
                self.okta_app_ids[app.app_id] = self._oid("0oa")
        it_emails = [p.email for p in self.by_dept["IT"]]
        for p in self.people:
            if p.kind == "rehire_old":
                continue
            if p.kind == "clean_former":
                p.okta, p.google, p.slack = "DEPROVISIONED", "suspended", "inactive"
            elif p.kind == "orphan_former":
                p.okta = "ACTIVE" if p.orphan_where == "okta" else ""
            else:
                p.okta, p.google, p.slack = "ACTIVE", "active", "active"
            p.login = self._login_date(p)
            p.login_ts = self._ts(p.login)
            created = f"{p.start.isoformat()}T09:00:00.000Z"
            local = _local(p.email)
            if p.okta:
                uid = self._oid("00u")
                self.okta_users.append(
                    OktaUser(uid, p.okta, created, p.login_ts, p.email, p.email, p.given, p.family)
                )
                self._owner("okta", p.email, p.eid)
                for app, role in p.sso.items():
                    self._assign(p, uid, app, role, it_emails)
            if p.google:
                self.google_users.append(
                    GoogleUser(
                        id=str(r.randrange(10**20, 10**21)),
                        primary_email=p.email,
                        given_name=p.given,
                        family_name=p.family,
                        full_name=f"{p.given} {p.family}",
                        suspended=p.google == "suspended",
                        org_unit_path=f"/Meridian/{p.dept}",
                        aliases=(p.old_email,) if p.old_email else (),
                        creation_time=f"{p.start.isoformat()}T09:05:00.000Z",
                        last_login_time=p.login_ts,
                    )
                )
                self._owner("google", p.email, p.eid)
            if p.slack:
                self.slack_users.append(
                    SlackScimUser(
                        self._oid("U", 10, _ALNUM_UPPER),
                        local,
                        p.display,
                        p.email,
                        p.slack == "active",
                    )
                )
                self._owner("slack", local, p.eid)
            self._account_findings(p)

    def _assign(self, p: _P, uid: str, app: str, role: str, it_emails: list[str]) -> None:
        r = self.rng
        okta_app = self.okta_app_ids[app]
        drifted = [d for d in p.drift if d[0] == app]
        if drifted:
            _, _, day = drifted[0]
            created = self._ts(_day(day))
            actor, actor_type, via = r.choice(it_emails), "User", "admin console"
            kind = _PRIVILEGE if role == "admin" else _ADD
            self.assign.append((okta_app, uid, created, "USER", role))
        else:
            created = f"{p.sso_created[app].isoformat()}T09:00:00.000Z"
            self.assign.append((okta_app, uid, created, "GROUP", role))
            new_this_month = p.sso_created[app] >= _MONTH
            if not (p.kind in ("hire", "rehire_new") or (p.kind == "mover" and new_this_month)):
                return
            birthright = app in cat.BIRTHRIGHT_IDS
            actor = _BIRTHRIGHT_RULE if birthright else _PROVISIONER
            actor_type, via, kind = (
                "SystemPrincipal",
                "group rule" if birthright else "provisioner",
                _ADD,
            )
        self.log.append(
            OktaLogEvent(
                uuid=str(uuid.UUID(int=r.getrandbits(128), version=4)),
                published=created,
                event_type=kind,
                actor_alternate_id=actor,
                actor_type=actor_type,
                via=via,
                target_app_id=okta_app,
                target_user_id=uid,
                target_user_login=p.email,
            )
        )

    def _account_findings(self, p: _P) -> None:
        k, em, name = p.kind, p.email, p.display
        slack = _local(em)
        if k == "orphan_former" and p.orphan_where == "okta":
            self._find(
                "orphaned_account",
                p.eid,
                em,
                "okta",
                "",
                "remove",
                0,
                f"{name} left before November but still has an active Okta account.",
            )
        elif k in ("leaver", "past_end"):
            suspend = k == "leaver"
            ftype = "leaver_access_left" if suspend else "contractor_past_end"
            day = p.day if suspend else self._end_day(p)
            why = (
                f"{name} left on {p.end}."
                if suspend
                else f"Contractor {name}'s end date {p.end} has passed but HRIS still says active."
            )
            self._find(ftype, p.eid, em, "okta", "", "suspend" if suspend else "remove", day, why)
            self._find(ftype, p.eid, em, "google", "", "suspend" if suspend else "remove", day, why)
            self._find(ftype, p.eid, slack, "slack", "", "remove", day, why)
            for app in p.sso:
                self._find(ftype, p.eid, em, "okta", app, "remove", day, f"{why} Still assigned.")
        elif k == "leave":
            why = f"{name} is on leave from {p.leave_start}; suspend, don't remove."
            self._find("leave_of_absence", p.eid, em, "okta", "", "suspend", p.day, why)
            self._find("leave_of_absence", p.eid, em, "google", "", "suspend", p.day, why)
            self._find("leave_of_absence", p.eid, slack, "slack", "", "suspend", p.day, why)
        elif k == "mover":
            old = f"{name} moved to {p.dept} on {_day(p.day)}."
            for app in p.stale_sso:
                self._find("mover_stale_access", p.eid, em, "okta", app, "remove", p.day, old)
            for app in p.missing_sso:
                self._find("mover_stale_access", p.eid, em, "okta", app, "add", p.day, old)
            for app in p.missing_other:
                self._find("mover_stale_access", p.eid, em, app, app, "add", p.day, old)
        elif k == "drift":
            for app, role, day in p.drift:
                self._find(
                    "drift",
                    p.eid,
                    em,
                    "okta",
                    app,
                    "flag" if role == "admin" else "remove",
                    day,
                    f"An admin granted {name} {role} access by hand on {_day(day)}; their role "
                    "does not include this app.",
                )

    # ---- unowned accounts: break-glass and nobody -------------------------------------

    def _ghosts(self) -> None:
        r, rc = self.rng, self.rc
        admin_apps = [a for a in cat.SSO_IDS if cat.APP_BY_ID[a].has_admin_role]
        for i in range(1, rc.break_glass + 1):
            email = f"breakglass-{i:02d}@{DOMAIN}"
            created = f"{date(2024, 3, 1) + timedelta(days=14 * i)}T09:00:00.000Z"
            last = f"{date(2026, r.randint(1, 9), r.randint(1, 28))}T09:00:00.000Z"
            uid = self._oid("00u")
            self.okta_users.append(
                OktaUser(
                    uid, "ACTIVE", created, last, email, email, "Breakglass", f"Account {i:02d}"
                )
            )
            app = admin_apps[i * 7 % len(admin_apps)]
            self.assign.append((self.okta_app_ids[app], uid, created, "USER", "admin"))
            self.google_users.append(
                GoogleUser(
                    str(r.randrange(10**20, 10**21)),
                    email,
                    "Breakglass",
                    f"Account {i:02d}",
                    f"Breakglass Account {i:02d}",
                    False,
                    "/Meridian/IT",
                    (),
                    created,
                    last,
                )
            )
            self._owner("okta", email, "")
            self._owner("google", email, "")
            self.break_glass.append(email)
            self._find(
                "break_glass",
                "",
                email,
                "okta",
                "",
                "never_touch",
                0,
                f"Break-glass account {email}: report only, never change.",
            )
        nobody = rc.orphaned_accounts - rc.orphaned_former_employees
        places = ("okta", "google", "slack", "export", "export", "okta")
        for i in range(nobody):
            local, display = (
                cat.NOBODY_ACCOUNTS[i]
                if i < len(cat.NOBODY_ACCOUNTS)
                else (f"stale.account{i + 1}", f"Stale Account {i + 1}")
            )
            email = f"{local}@{DOMAIN}"
            given, _, family = display.partition(" ")
            created = f"{date(2025, 1 + i, 10)}T09:00:00.000Z"
            why = f"{email} matches nobody in HRIS."
            where = places[i % len(places)]
            if where == "okta":
                uid = self._oid("00u")
                last = self._ts(date(2026, r.randint(1, 10), r.randint(1, 28)))
                self.okta_users.append(
                    OktaUser(uid, "ACTIVE", created, last, email, email, given, family)
                )
                app = r.choice(cat.SSO_IDS)
                self.assign.append((self.okta_app_ids[app], uid, created, "USER", "member"))
                self._owner("okta", email, "")
                self._find("orphaned_account", "", email, "okta", "", "remove", 0, why)
            elif where == "google":
                self.google_users.append(
                    GoogleUser(
                        str(r.randrange(10**20, 10**21)),
                        email,
                        given,
                        family,
                        display,
                        False,
                        "/Meridian",
                        (),
                        created,
                        created,
                    )
                )
                self._owner("google", email, "")
                self._find("orphaned_account", "", email, "google", "", "remove", 0, why)
            elif where == "slack":
                self.slack_users.append(
                    SlackScimUser(self._oid("U", 10, _ALNUM_UPPER), local, display, email, True)
                )
                self._owner("slack", local, "")
                self._find("orphaned_account", "", local, "slack", "", "remove", 0, why)
            else:
                when = date(2026, r.randint(1, 9), r.randint(1, 28))
                entry = _Entry(
                    r.choice(cat.NON_SSO_IDS),
                    "",
                    given,
                    family,
                    display,
                    email,
                    "",
                    when,
                    10_000 + i,
                    username=local,
                )
                entry.templates.append(("orphaned_account", "flag", 0, why))
                self.entries.append(entry)
        mass = rc.mass_termination
        for event in range(mass.events):
            self._find(
                "hris_mass_termination",
                "",
                "",
                "hris",
                "",
                "block_two_approvers",
                mass.day - event,
                f"A bulk import marked {mass.people} people in {mass.department} as terminated "
                "at once; they are still logging in.",
            )

    # ---- 7. exports -------------------------------------------------------------------

    def _mess(self, email: str) -> str:
        x = self.rng.random()
        if x < 0.08:
            local, _, domain = email.partition("@")
            return ".".join(part.capitalize() for part in local.split(".")) + "@" + domain
        if x < 0.14:
            return email + " "
        return email

    def _exports(self) -> tuple[AppExport, ...]:
        r = self.rng
        for p in self.people:
            for app in p.exports:
                old = app in p.old_email_apps
                family = p.old_family if old else p.family
                when = max(p.start, p.login - timedelta(days=r.randint(0, 20)))
                entry = _Entry(
                    app,
                    p.eid,
                    p.given,
                    family,
                    f"{p.preferred or p.given} {family}",
                    p.old_email if old else p.email,
                    p.dept,
                    when,
                    p.order,
                )
                if app in p.row_findings:
                    entry.templates.append(p.row_findings[app])
                self.entries.append(entry)
        by_app: dict[str, list[_Entry]] = {a: [] for a in cat.NON_SSO_IDS}
        for e in self.entries:
            by_app[e.app].append(e)
        plan = self._layout_plan()
        headers = {
            "A": ("Email", "Full Name", "Role", "Last Login"),
            "B": ("user_email", "name", "permission", "last_active_at", "department"),
            "C": ("Login", "Display Name", "Access Level", "Created"),
            "D": ("Username", "First", "Last", "Status", "Added On"),
        }
        perms = {
            "A": ("Member",) * 5 + ("Viewer", "Editor", "Admin"),
            "B": ("read",) * 4 + ("write",) * 3 + ("admin",),
            "C": ("Standard",) * 5 + ("Power User", "Power User", "Admin"),
            "D": ("Active",) * 7 + ("Invited",),
        }
        rendered: list[tuple[str, dict[str, str], list[tuple[str, ...]]]] = []
        for app in cat.NON_SSO_IDS:
            info = plan[app]
            layout = info["layout"]
            entries = by_app[app]
            names: dict[int, str] = {}
            if layout == "D":
                used: set[str] = set()
                for e in sorted(entries, key=lambda e: (e.order, e.display)):
                    base = e.username or _username(info["scheme"], e.given, e.family, e.email)
                    name, n = base, 2
                    while name in used:
                        name, n = f"{base}{n}", n + 1
                    used.add(name)
                    names[id(e)] = name
            rows: list[tuple[str, ...]] = []
            for e in sorted(entries, key=lambda e: (e.display.lower(), e.email.lower(), e.order)):
                when = _fmt_date(e.when, info["format"])
                perm = r.choice(perms[layout])
                if layout == "D":
                    account = names[id(e)]
                    rows.append((account, e.given, e.family, perm, when))
                else:
                    account = self._mess(e.email)
                    if layout == "A":
                        rows.append((account, e.display, perm, when))
                    elif layout == "B":
                        rows.append((account, e.display, perm, when, e.dept))
                    else:
                        rows.append((account, e.display, perm, when))
                self._owner(app, account, e.owner)
                for ftype, action, day, detail in e.templates:
                    self._find(ftype, e.owner, account, app, app, action, day, detail)
            width = len(headers[layout])
            for _ in range(r.randint(0, 2)):
                rows.insert(r.randint(0, len(rows)), ("",) * width)
            rendered.append((app, info, rows))
        return self._finish_exports(rendered, headers)

    def _finish_exports(
        self,
        rendered: list[tuple[str, dict[str, str], list[tuple[str, ...]]]],
        headers: dict[str, tuple[str, ...]],
    ) -> tuple[AppExport, ...]:
        modulus, remainder = cat.BOM_MODULUS, cat.BOM_REMAINDER
        candidates = [
            j
            for j, (_, _, rows) in enumerate(rendered)
            if j % modulus != remainder and not "".join("".join(row) for row in rows).isascii()
        ]
        k = self.rc.cp1252_exports
        if len(candidates) < k:
            raise ValueError("not enough exports with a non-ASCII name for the cp1252 exports")
        legacy = {candidates[i * len(candidates) // k] for i in range(k)} if k else set()
        out = []
        for j, (app, info, rows) in enumerate(rendered):
            encoding = (
                "cp1252" if j in legacy else "utf-8-sig" if j % modulus == remainder else "utf-8"
            )
            out.append(
                AppExport(
                    app,
                    f"{app}.csv",
                    encoding,
                    info["layout"],
                    info["format"],
                    headers[info["layout"]],
                    tuple(rows),
                )
            )
        return tuple(out)

    # ---- HRIS -------------------------------------------------------------------------

    def _hris(self) -> tuple[tuple[HrisRecord, ...], tuple[HrisEvent, ...]]:
        records = []
        events = []

        def event(p: _P, day: int, kind: str, fields: tuple[tuple[str, str], ...], src: str = "hr"):
            events.append(HrisEvent(day, _day(day).isoformat(), p.eid, kind, fields, src))

        for p in self.people:
            records.append(
                HrisRecord(
                    employee_id=p.eid,
                    given_name=p.given,
                    family_name=p.family,
                    preferred_name=p.preferred,
                    work_email=p.email,
                    personal_email=p.personal,
                    job_title=p.title,
                    department=p.dept,
                    team=cat.ROLE_BY_ID[p.role].team,
                    manager_id=p.manager_id,
                    country=p.country,
                    hub=cat.HUB_OF[p.country],
                    employment_type="contractor" if p.contractor else "employee",
                    status=p.status,
                    start_date=p.start.isoformat(),
                    end_date=p.end.isoformat() if p.end else "",
                    leave_start=p.leave_start.isoformat() if p.leave_start else "",
                    leave_end=p.leave_end.isoformat() if p.leave_end else "",
                )
            )
            role = cat.ROLE_BY_ID[p.role]
            move = (("department", p.dept), ("job_title", p.title), ("team", role.team))
            if p.kind == "hire":
                event(p, p.day, "hire", (*move, ("start_date", p.start.isoformat())))
            elif p.kind == "rehire_new":
                event(p, p.day, "rehire", (*move, ("work_email", p.email)))
            elif p.kind == "mover":
                event(p, p.day, "move", move)
            elif p.kind == "leaver":
                event(p, p.day, "terminate", (("end_date", _req(p.end).isoformat()),))
            elif p.kind == "mass":
                event(
                    p, p.day, "terminate", (("end_date", _day(p.day).isoformat()),), "bulk_import"
                )
            elif p.kind == "leave":
                event(
                    p,
                    p.day,
                    "leave_start",
                    (
                        ("leave_start", _req(p.leave_start).isoformat()),
                        ("leave_end", _req(p.leave_end).isoformat()),
                    ),
                )
            elif p.kind == "namechg":
                event(p, p.day, "name_change", (("family_name", p.family), ("work_email", p.email)))
        return (
            tuple(sorted(records, key=lambda x: x.employee_id)),
            tuple(sorted(events, key=lambda x: (x.day, x.employee_id, x.kind))),
        )


def generate_access(seed: int = ACCESS_TUNING_SEED, recipe: Recipe | None = None) -> AccessWorld:
    """Build the access world for a seed. The default recipe is the tuning one."""
    recipe = recipe if recipe is not None else load_recipe("tuning")
    validate_recipe(recipe)
    return _Builder(seed, recipe).build()
