"""The dry-run policy engine: notice first, then a plan a person reviews. Nothing is applied.

The catalogue's rule: "auto-revoke dormant restricted-scope grants after a 7-day notice;
route everything else to a human", with a "known-compromised app" emergency rule that needs
a human trigger, and an exception list with an expiry date. Here every one of those is a
**plan or a draft, never a change**. The radar has no call that changes a grant (the mock
API has none either), and a plan says `"applied": false`. Applying a plan would need a
decision that supersedes OAR-004.

For each scored grant, on the simulated date `today`, in this order:

1. **Compromised app** (on Security's list): goes into the **emergency plan**, which skips the
   notice but must be triggered by a person. The radar never triggers it.
2. **A current exception** covers the grant (same app, and the same user or every user, and
   `today` is on or before its expiry date): no action, listed as excepted.
3. **Dormant with a restricted scope** (last used more than `dormant_days` ago, and at least
   one scope Google classes as restricted):
   - no notice yet: a **notice draft** is written, dated today;
   - a notice under `notice_days` old: waiting;
   - a notice at least `notice_days` old, and the grant not used since: into the **plan**.
   A grant used after its notice is no longer dormant, and its notice lapses.
4. **Restricted, but no record of use**: to a person (the radar can't tell if it's dormant).
5. **High or medium** by the rubric: to a person.
6. Low: no action.

**Blast-radius limit.** A plan may touch at most `max_grants_per_plan` grants. A plan over the
limit is refused whole: it lists nothing to do, and every grant it would have touched goes
to a person instead.

**Idempotent.** The same scores, notice ledger, exceptions, compromised list, settings and
date give the same result, byte for byte, and a notice is never issued twice. The notice
ledger the result carries is the input of the next run.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from meridian_oauth_radar import scopes
from meridian_oauth_radar.collector import TokenGrant
from meridian_oauth_radar.load import ExportError
from meridian_oauth_radar.rubric import HIGH, MEDIUM, Score

# What a plan proposes for each grant. Data only: nothing in the radar carries it out.
PLAN_ACTION = "revoke"
HOW_BY_HAND = (
    "In the Google Admin console, remove this app's access for this user "
    "(Directory API tokens.delete for this userKey and clientId)."
)
AFTER_NOTICE, EMERGENCY = "after-notice", "emergency"
PROPOSED, REFUSED, EMPTY = "proposed", "refused", "empty"


@dataclass(frozen=True, slots=True)
class Settings:
    dormant_days: int = 90
    notice_days: int = 7
    max_grants_per_plan: int = 10

    def __post_init__(self) -> None:
        if self.dormant_days < 1 or self.notice_days < 1 or self.max_grants_per_plan < 1:
            raise ValueError("every policy setting must be at least 1")


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    """An approved business need: IT keeps this app (for one user, or everyone) until expiry."""

    client_id: str
    user_key: str | None
    reason: str
    approved_by: str
    expires: date

    def covers(self, grant: TokenGrant) -> bool:
        return grant.client_id == self.client_id and self.user_key in (None, grant.user_key)

    def current(self, today: date) -> bool:
        return today <= self.expires


@dataclass(frozen=True, slots=True)
class Advisory:
    client_id: str
    name: str
    advisory: str
    published: date


@dataclass(frozen=True, slots=True)
class Notice:
    """A notice draft: the user is told the grant will be removed after the notice period."""

    user_key: str
    client_id: str
    app: str
    scopes: tuple[str, ...]
    issued: date


@dataclass(frozen=True, slots=True)
class PlanItem:
    user_key: str
    client_id: str
    app: str
    scopes: tuple[str, ...]
    reason: str
    notice_issued: date | None
    action: str = PLAN_ACTION
    how: str = HOW_BY_HAND


@dataclass(frozen=True, slots=True)
class Plan:
    kind: str
    status: str
    items: tuple[PlanItem, ...]
    would_touch: int
    max_grants: int
    note: str
    requires_human_trigger: bool

    @property
    def plan_id(self) -> str:
        """A hash of what the plan would do: the same plan always has the same ID."""
        return hashlib.sha256(_dump(_plan_body(self))).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Routed:
    """A grant for a person to look at, and why."""

    score: Score
    reason: str


@dataclass(frozen=True, slots=True)
class Excepted:
    grant: TokenGrant
    entry: ExceptionEntry


@dataclass(frozen=True, slots=True)
class PolicyResult:
    today: date
    settings: Settings
    new_notices: tuple[Notice, ...]
    waiting: tuple[Notice, ...]
    ledger: tuple[Notice, ...]
    plan: Plan
    emergency: Plan
    to_a_person: tuple[Routed, ...]
    excepted: tuple[Excepted, ...]
    expired_exceptions: tuple[ExceptionEntry, ...]
    no_action: int
    dormant_restricted: int


def _order(notice: Notice) -> tuple[str, str, str]:
    return (notice.app.casefold(), notice.user_key, notice.client_id)


def days_unused(grant: TokenGrant, today: date) -> int | None:
    if grant.last_used is None:
        return None
    return (today - grant.last_used.date()).days


def is_dormant(grant: TokenGrant, today: date, settings: Settings) -> bool:
    unused = days_unused(grant, today)
    return unused is not None and unused > settings.dormant_days


def restricted(grant: TokenGrant) -> tuple[str, ...]:
    return tuple(s for s in grant.scopes if scopes.is_restricted(s))


def _limit(kind: str, items: list[PlanItem], settings: Settings, trigger: bool) -> Plan:
    items.sort(key=lambda i: (i.app.casefold(), i.user_key, i.client_id))
    limit = settings.max_grants_per_plan
    if not items:
        return Plan(kind, EMPTY, (), 0, limit, "Nothing to do.", trigger)
    if len(items) > limit:
        note = (
            f"Refused: it would touch {len(items)} grants, over the blast-radius limit of "
            f"{limit}. Each grant goes to a person instead."
        )
        return Plan(kind, REFUSED, (), len(items), limit, note, trigger)
    note = (
        "A dry run. Nothing has been applied. A person reviews this plan"
        + (" and must trigger it" if trigger else "")
        + " before anyone removes anything, by hand."
    )
    return Plan(kind, PROPOSED, tuple(items), len(items), limit, note, trigger)


def decide(
    found: Iterable[Score],
    *,
    today: date,
    ledger: Iterable[Notice] = (),
    exceptions: Iterable[ExceptionEntry] = (),
    compromised: Mapping[str, Advisory] | None = None,
    settings: Settings | None = None,
) -> PolicyResult:
    """Apply the policy to every scored grant on `today`. Pure: it reads and returns."""
    settings = settings or Settings()
    compromised = compromised or {}
    exceptions = tuple(exceptions)
    earlier = {(n.user_key, n.client_id): n for n in ledger}
    emergency: list[PlanItem] = []
    after_notice: list[tuple[PlanItem, Score]] = []
    emergency_scores: list[Score] = []
    new_notices: list[Notice] = []
    waiting: list[Notice] = []
    kept: list[Notice] = []
    routed: list[Routed] = []
    excepted: list[Excepted] = []
    no_action = 0
    dormant_restricted = 0

    for score in found:
        grant = score.grant
        held = restricted(grant)
        advisory = compromised.get(grant.client_id)
        if advisory is not None:
            emergency.append(
                PlanItem(
                    grant.user_key,
                    grant.client_id,
                    grant.display_text,
                    grant.scopes,
                    f"Security lists the app as compromised ({advisory.advisory}, "
                    f"{advisory.published.isoformat()}).",
                    None,
                )
            )
            emergency_scores.append(score)
            continue
        cover = next((e for e in exceptions if e.covers(grant) and e.current(today)), None)
        if cover is not None:
            excepted.append(Excepted(grant, cover))
            continue
        if held and is_dormant(grant, today, settings):
            dormant_restricted += 1
            notice = earlier.get(grant.key)
            if notice is None or grant.last_used is None or grant.last_used.date() > notice.issued:
                notice = Notice(
                    grant.user_key, grant.client_id, grant.display_text, grant.scopes, today
                )
                new_notices.append(notice)
                kept.append(notice)
            elif (today - notice.issued).days < settings.notice_days:
                waiting.append(notice)
                kept.append(notice)
            else:
                unused = days_unused(grant, today)
                after_notice.append(
                    (
                        PlanItem(
                            grant.user_key,
                            grant.client_id,
                            grant.display_text,
                            grant.scopes,
                            f"Not used for {unused} days, holds a restricted scope "
                            f"({', '.join(held)}), and the notice of "
                            f"{notice.issued.isoformat()} has run {settings.notice_days} days.",
                            notice.issued,
                        ),
                        score,
                    )
                )
                kept.append(notice)
            continue
        if held and grant.last_used is None:
            routed.append(Routed(score, "Holds a restricted scope, with no record of use."))
            continue
        if score.tier in (HIGH, MEDIUM):
            routed.append(
                Routed(score, f"Rated {score.tier} by the rubric ({score.total} points).")
            )
            continue
        no_action += 1

    plan = _limit(AFTER_NOTICE, [item for item, _ in after_notice], settings, trigger=False)
    if plan.status == REFUSED:
        routed.extend(
            Routed(score, "Its removal plan was refused by the blast-radius limit.")
            for _, score in after_notice
        )
    urgent = _limit(EMERGENCY, emergency, settings, trigger=True)
    if urgent.status == REFUSED:
        routed.extend(
            Routed(score, "Compromised app; the emergency plan was over the blast-radius limit.")
            for score in emergency_scores
        )
    return PolicyResult(
        today=today,
        settings=settings,
        new_notices=tuple(sorted(new_notices, key=_order)),
        waiting=tuple(sorted(waiting, key=_order)),
        ledger=tuple(sorted(kept, key=_order)),
        plan=plan,
        emergency=urgent,
        to_a_person=tuple(
            sorted(
                routed,
                key=lambda r: (
                    r.score.tier != HIGH,
                    -r.score.total,
                    r.score.grant.display_text.casefold(),
                    r.score.grant.key,
                ),
            )
        ),
        excepted=tuple(
            sorted(excepted, key=lambda e: (e.grant.display_text.casefold(), e.grant.key))
        ),
        expired_exceptions=tuple(
            sorted(
                (e for e in exceptions if not e.current(today)),
                key=lambda e: (e.client_id, e.user_key or ""),
            )
        ),
        no_action=no_action,
        dormant_restricted=dormant_restricted,
    )


# Files: the plans, the notice drafts, the ledger and the list for a person.


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _plan_body(plan: Plan) -> dict[str, Any]:
    return {
        "kind": plan.kind,
        "status": plan.status,
        "dry_run": True,
        "applied": False,
        "requires_human_trigger": plan.requires_human_trigger,
        "max_grants": plan.max_grants,
        "would_touch": plan.would_touch,
        "note": plan.note,
        "items": [
            {
                "user_key": i.user_key,
                "client_id": i.client_id,
                "app": i.app,
                "scopes": list(i.scopes),
                "action": i.action,
                "reason": i.reason,
                "notice_issued": i.notice_issued.isoformat() if i.notice_issued else None,
                "how_by_hand": i.how,
            }
            for i in plan.items
        ],
    }


def plan_json(plan: Plan, today: date) -> bytes:
    return _dump({"plan_id": plan.plan_id, "for_date": today.isoformat(), **_plan_body(plan)})


def notice_text(notice: Notice, settings: Settings) -> str:
    removal = notice.issued + timedelta(days=settings.notice_days)
    can = "; ".join(scopes.can(s) for s in notice.scopes if scopes.is_restricted(s))
    return (
        f"**{notice.app}** still has access to your Google account. It can {can}. "
        f"It hasn't been used for more than {settings.dormant_days} days. "
        f"If you still need it, use it or reply to IT before {removal.isoformat()}. "
        f"After that, IT plans to remove its access; you can always connect it again. "
        "Nothing has been removed yet."
    )


def ledger_json(result: PolicyResult) -> bytes:
    return _dump(
        {
            "for_date": result.today.isoformat(),
            "notices": [
                {
                    "user_key": n.user_key,
                    "client_id": n.client_id,
                    "app": n.app,
                    "scopes": list(n.scopes),
                    "issued": n.issued.isoformat(),
                }
                for n in result.ledger
            ],
        }
    )


def policy_files(result: PolicyResult) -> dict[str, bytes]:
    """Every file the policy writes, by path: plans, notices, the ledger, the list for a person."""
    files = {
        "plans/after-notice-plan.json": plan_json(result.plan, result.today),
        "plans/emergency-plan.json": plan_json(result.emergency, result.today),
        "notices/ledger.json": ledger_json(result),
    }
    by_user: dict[str, list[Notice]] = {}
    for notice in result.new_notices:
        by_user.setdefault(notice.user_key, []).append(notice)
    for user, notices in sorted(by_user.items()):
        lines = [
            f"# Notice draft for {user}",
            "",
            f"Draft of {result.today.isoformat()}. Not sent: the radar sends nothing.",
            "",
        ]
        lines += [f"- {notice_text(n, result.settings)}" for n in notices]
        files[f"notices/{user}.md"] = ("\n".join(lines) + "\n").encode()
    files["to-a-person.json"] = _dump(
        {
            "for_date": result.today.isoformat(),
            "grants": [
                {
                    "user_key": r.score.grant.user_key,
                    "client_id": r.score.grant.client_id,
                    "app": r.score.grant.display_text,
                    "tier": r.score.tier,
                    "total": r.score.total,
                    "reason": r.reason,
                }
                for r in result.to_a_person
            ],
        }
    )
    return files


def _date(path: Path, where: str, row: Mapping[str, Any], key: str) -> date:
    value = row.get(key)
    try:
        return date.fromisoformat(value) if isinstance(value, str) else _bad(path, where, key)
    except ValueError:
        return _bad(path, where, key)


def _bad(path: Path, where: str, key: str) -> date:
    raise ExportError(f'{path}: {where}: "{key}" must be a date (YYYY-MM-DD)')


def _text(path: Path, where: str, row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExportError(f'{path}: {where}: "{key}" must be a non-empty string')
    return value


def _rows(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExportError(f"{path}: can't read it as JSON ({err})") from err
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ExportError(f'{path}: needs a "{key}" list of objects')
    return rows


def _scopes(path: Path, where: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    value = row.get("scopes")
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        raise ExportError(f'{path}: {where}: "scopes" must be a list of strings')
    return tuple(sorted(set(value)))


def load_exceptions(path: Path) -> tuple[ExceptionEntry, ...]:
    """IT's exception list. Every entry needs an expiry date."""
    entries = []
    for i, row in enumerate(_rows(path, "exceptions")):
        where = f"exception {i}"
        user = row.get("user_key")
        if user is not None and (not isinstance(user, str) or not user.strip()):
            raise ExportError(f'{path}: {where}: "user_key" must be a string or null')
        entries.append(
            ExceptionEntry(
                client_id=_text(path, where, row, "client_id"),
                user_key=user,
                reason=_text(path, where, row, "reason"),
                approved_by=_text(path, where, row, "approved_by"),
                expires=_date(path, where, row, "expires"),
            )
        )
    return tuple(entries)


def load_compromised(path: Path) -> dict[str, Advisory]:
    """Security's list of known-compromised apps, by client ID."""
    found: dict[str, Advisory] = {}
    for i, row in enumerate(_rows(path, "apps")):
        where = f"app {i}"
        advisory = Advisory(
            client_id=_text(path, where, row, "client_id"),
            name=_text(path, where, row, "name"),
            advisory=_text(path, where, row, "advisory"),
            published=_date(path, where, row, "published"),
        )
        found[advisory.client_id] = advisory
    return found


def load_ledger(path: Path) -> tuple[Notice, ...]:
    """The notice ledger a previous run wrote. A missing file means no notices yet."""
    if not path.exists():
        return ()
    notices = []
    for i, row in enumerate(_rows(path, "notices")):
        where = f"notice {i}"
        notices.append(
            Notice(
                user_key=_text(path, where, row, "user_key"),
                client_id=_text(path, where, row, "client_id"),
                app=_text(path, where, row, "app"),
                scopes=_scopes(path, where, row),
                issued=_date(path, where, row, "issued"),
            )
        )
    return tuple(notices)


__all__ = [
    "AFTER_NOTICE",
    "EMERGENCY",
    "PLAN_ACTION",
    "Advisory",
    "ExceptionEntry",
    "Notice",
    "Plan",
    "PlanItem",
    "PolicyResult",
    "Settings",
    "decide",
    "load_compromised",
    "load_exceptions",
    "load_ledger",
    "plan_json",
    "policy_files",
]
