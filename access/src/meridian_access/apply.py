"""Apply a plan, safely, to the in-process mocks (AAC-007).

There is no mode for real systems. `mode` is `dry-run` (the default: say what would be done,
change nothing) or `mock-live` (change the in-process mocks). Any other value is a dry run.
Apply never talks to anything but the mocks it is given.

Before any change it re-checks what the pull request check already checked: the plan has
enough approvals (the requirement is recomputed from the changes and the policy, never taken
from the plan file alone) and is not stale (the actual state still has the hash the plan was
built from). It then rebuilds the plan from the current world, state and policy and refuses
when the changes, the policy hash or the desired-state hash differ from the approved plan, so
what was approved is what gets applied. A plan file is one part of the split plan, "main" or
"held" (its `part`); the rebuild splits the fresh plan and compares against the same part, and
a plan with no known part is refused. The held part needs `approvals_over_n` approvals and the
main part `approvals_default`. Changes held for bad HR data are never applied. Applying either
part makes the other one stale: both were built from the same actual state, and the apply
changes it, so the other part is refused as "stale" and must be built again and approved
again. A final break-glass
guard drops any change that names a break-glass account, whatever the diff did. Every change
checks the current state first, so a second run does nothing. Suspension comes before removal
where the API allows it.
A change names one account (system, app, name). Names are compared with case and spaces
ignored, as the diff does; when two or more accounts match, the one whose name is exactly the
change's is meant, and when no single one is, the change is failed and none of them is changed.
An Okta app add is failed, and nobody is assigned, when two or more Okta apps carry the app's
name.
After a mock-live apply that sent any write, the state is read again and every applied change is
checked to have taken effect; one that did not is reported as failed. Every account that changed
during the apply but that no change of the plan names (a write that reached another account) is
reported too (`unexpected`), and the command line exits 1.

    ACCESS_APPLY_MODE=mock-live python -m meridian_access.apply --plan plan.json \\
        --world out/access/seed-6108 --approvals 1

The command line applies only the main part. The held part needs two code-owner approvals on
its own pull request, and the command line takes the approval count from the caller, so it
refuses a held plan; `apply_plan` (the function) supports the held part.
"""

import argparse
import collections
import dataclasses
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meridian_access.approvals import required_for, version_message
from meridian_access.collect import ActualState, HttpishClient, Mocks, actual_state
from meridian_access.exports import parse_exports
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock, Token
from meridian_access.models import Account, CollectorError
from meridian_access.plan import PLAN_VERSION, Plan, build_plan, canonical_json, split_plan
from meridian_access.plan import manual_tasks as plan_manual_tasks
from meridian_access.policy.load import Policy, load_policy
from meridian_dataset.access.model import DOMAIN

PARTS = ("main", "held")
DRY_RUN = "dry-run"
MOCK_LIVE = "mock-live"
ACCEPTED_MODES = frozenset({DRY_RUN, MOCK_LIVE})
MODE_ENV = "ACCESS_APPLY_MODE"
DIRECTORY = ("okta", "google", "slack")
MAX_RETRIES = 6
# Execution order: lock accounts first (suspend), then take app access away, then give the
# access a mover now needs, then the removals that end an account.
_SUSPEND, _UNASSIGN, _ADD, _END = 0, 1, 2, 3
# The only 404s that mean "already done": an app unassignment of an assignment that is gone,
# and a Slack SCIM or Google Directory user that is gone. Any other 404 (a wrong route, an
# unknown Okta user) is a failure.
_GONE_OK = (
    ("okta", "DELETE", re.compile(r"/api/v1/apps/[^/]+/users/[^/]+")),
    ("slack", "PATCH", re.compile(r"/scim/v2/Users/[^/]+")),
    ("google", "PATCH", re.compile(r"/admin/directory/v1/users/[^/]+")),
)


def normalise_mode(value: str | None) -> str:
    """The mode to use: `mock-live` only for exactly that value; anything else is a dry run."""
    return MOCK_LIVE if value == MOCK_LIVE else DRY_RUN


@dataclass(slots=True)
class ApplyResult:
    applied: list[dict[str, Any]] = field(default_factory=list)
    skipped_already_done: list[dict[str, Any]] = field(default_factory=list)
    dropped_break_glass: list[dict[str, Any]] = field(default_factory=list)
    manual_tasks: list[dict[str, str]] = field(default_factory=list)
    refused_reason: str = ""
    log: list[str] = field(default_factory=list)
    mode: str = DRY_RUN
    failed: list[dict[str, Any]] = field(default_factory=list)
    flagged_for_review: list[dict[str, Any]] = field(default_factory=list)  # drift flags
    deferred_deletes: list[dict[str, Any]] = field(default_factory=list)  # "delete in a later plan"
    not_applied: list[dict[str, Any]] = field(default_factory=list)  # adds, HR flags, ...
    held_not_applied: int = 0
    # accounts that changed during a mock-live apply although no change of the plan names them
    unexpected: list[dict[str, Any]] = field(default_factory=list)
    writes_sent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "refused_reason": self.refused_reason,
            "applied": self.applied,
            "skipped_already_done": self.skipped_already_done,
            "dropped_break_glass": self.dropped_break_glass,
            "manual_tasks": self.manual_tasks,
            "failed": self.failed,
            "flagged_for_review": self.flagged_for_review,
            "deferred_deletes": self.deferred_deletes,
            "not_applied": self.not_applied,
            "held_not_applied": self.held_not_applied,
            "unexpected": self.unexpected,
            "log": self.log,
        }


class Ambiguous(Exception):
    """A change's name matches more than one account and does not tell which one it means."""

    def __init__(self, count: int) -> None:
        super().__init__(f"{count} accounts go by this name")
        self.count = count


class _Accounts:
    """The accounts of one state, found by the name a change gives.

    A change names an account by system, app and name; names are compared with case and spaces
    ignored, as the diff does. An account listed twice is one account. When the name matches
    one account, that is it. When it matches more, the one whose name is exactly the change's
    (spaces ignored) is it; when no single one is, `find` raises `Ambiguous`, and apply changes
    none of them: the list order must never pick the account."""

    def __init__(self, accounts: Iterable[Account]) -> None:
        self._by: dict[tuple[str, str, str], list[Account]] = {}
        for a in accounts:
            self.add(a)

    @staticmethod
    def _key(system: str, app_id: str, name: str) -> tuple[str, str, str]:
        return (system, app_id, _norm(name))

    def add(self, a: Account) -> None:
        group = self._by.setdefault(self._key(a.system, a.app_id, a.account), [])
        if a not in group:
            group.append(a)

    def find(self, system: str, app_id: str, name: str) -> Account | None:
        group = self._by.get(self._key(system, app_id, name), [])
        if len(group) < 2:
            return group[0] if group else None
        exact = [a for a in group if a.account.strip() == name.strip()]
        if len(exact) == 1:
            return exact[0]
        raise Ambiguous(len(group))

    def remove(self, old: Account) -> None:
        """Keep the local view in step with a write that ended `old` (an app assignment)."""
        self._by[self._key(old.system, old.app_id, old.account)].remove(old)

    def replace(self, old: Account, new: Account) -> None:
        """Keep the local view in step with a write that turned `old` into `new`."""
        self.remove(old)
        self.add(new)


def issue_write_tokens(mocks: Mocks) -> dict[str, Token]:
    """A read-write token for each mock (what an apply job would be given)."""
    return {
        name: mock.issue_token(f"apply-{name}", {"read", "write"})
        for name, mock in (("okta", mocks.okta), ("google", mocks.google), ("slack", mocks.slack))
    }


def _norm(text: str) -> str:
    return text.strip().casefold()


def _load_plan(plan: Plan | Path | str) -> Plan:
    if isinstance(plan, Plan):
        return plan
    return Plan(json.loads(Path(plan).read_text(encoding="utf-8")))


def _break_glass_names(state: ActualState, policy: Policy) -> set[str]:
    """Every name a break-glass account goes by: login, email, Google aliases."""
    names: set[str] = set()
    for a in state.accounts:
        values = [v for v in (a.account, a.email, *a.aliases) if v.strip()]
        if any(_is_listed(policy, v) for v in values):
            names |= {_norm(v) for v in values}
    return names


def _is_listed(policy: Policy, value: str) -> bool:
    return policy.is_break_glass(value) or (
        "@" not in value and policy.is_break_glass(f"{value.strip()}@{DOMAIN}")
    )


def _touches_break_glass(change: Mapping[str, Any], policy: Policy, names: set[str]) -> bool:
    account = change.get("account", "")
    if not account.strip():
        return False
    return _norm(account) in names or _is_listed(policy, account)


def _step(change: Mapping[str, Any]) -> int:
    system, app_id, action = change["system"], change["app_id"], change["action"]
    if action == "add":
        return _ADD
    if action == "suspend" and system in ("okta", "google"):
        return _SUSPEND
    if system == "okta" and app_id:
        return _UNASSIGN
    if system == "slack":
        return _UNASSIGN
    return _END


def apply_plan(
    plan: Plan | Path | str,
    mocks: Mocks,
    *,
    mode: str | None = DRY_RUN,
    approvals: int,
    policy: Policy,
    write_token: Mapping[str, Token] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    world_dir: Path | str | None = None,
    exports_dir: Path | str | None = None,
    policy_dir: Path | None = None,
    rebuild: Callable[[ActualState], Plan] | None = None,
) -> ApplyResult:
    """Apply `plan` to `mocks`. `write_token` maps okta, google and slack to read-write tokens
    (see `issue_write_tokens`). `world_dir` is the world directory (the HR data the plan is
    rebuilt from, and the exports); `exports_dir` defaults to its `exports/`. `policy_dir` is
    where `policy` was loaded from (default: the repository's). `rebuild` replaces the
    rebuild-from-the-world step, for tests that apply a hand-made plan: it returns the fresh
    plan of the SAME part as the plan being applied (the default rebuilds the combined plan,
    splits it, and picks that part; a part missing from the fresh split is "plan changed").
    Dry run unless `mode` is exactly `mock-live`."""
    mode = normalise_mode(mode)
    plan = _load_plan(plan)
    data = plan.data
    result = ApplyResult(mode=mode)
    result.log.append(f"mode: {mode}")
    result.held_not_applied = sum(1 for c in data.get("held", []) if c.get("hold") == "bad_data")
    if result.held_not_applied:
        result.log.append(
            f"{result.held_not_applied} changes held for bad HR data are never applied"
        )
    if exports_dir is None and world_dir is not None:
        exports_dir = Path(world_dir) / "exports"

    if data.get("version") != PLAN_VERSION:
        result.refused_reason = version_message(data.get("version"))
        result.log.append(f"refused: {result.refused_reason}")
        return result
    if data.get("part") not in PARTS:
        result.refused_reason = f"unknown plan part {data.get('part')!r}: expected main or held"
        result.log.append(f"refused: {result.refused_reason}")
        return result
    # what the plan file says is a floor, never the answer: recompute from its own changes
    try:
        need = max(int(data["approvals_required"]), required_for(data["changes"], policy))
        if data["part"] == "held":
            need = max(need, policy.settings.approvals_over_n)
    except ValueError as exc:
        result.refused_reason = f"cannot work out the approvals the plan needs: {exc}"
        result.log.append(f"refused: {result.refused_reason}")
        return result
    if approvals < need:
        result.refused_reason = f"needs {need} approvals"
        result.log.append(f"refused: needs {need} approvals, has {approvals}")
        return result
    try:
        state = actual_state(mocks, policy, exports_dir, sleep=sleep)
    except CollectorError as exc:
        result.refused_reason = f"cannot read the current state: {exc}"
        result.log.append(f"refused: {result.refused_reason}")
        return result
    if state.digest() != data["actual_state_sha256"]:
        result.refused_reason = "stale"
        result.log.append("refused: the actual state changed since the plan was built; re-plan")
        return result
    if rebuild is None:
        if world_dir is None:
            result.refused_reason = "no world to rebuild the plan from"
            result.log.append("refused: apply needs the world directory to re-check the plan")
            return result
        root = Path(world_dir)

        part = data["part"]

        def rebuild(st: ActualState) -> Plan:
            main, held = split_plan(
                build_plan(root, policy, policy_dir=policy_dir, state=st), policy
            )
            chosen = main if part == "main" else held
            if chosen is None:
                raise ValueError(f"a fresh build has no {part} plan")
            return chosen

    try:
        fresh = rebuild(state).data
    except (OSError, ValueError, KeyError, CollectorError) as exc:
        result.refused_reason = f"plan changed: cannot rebuild it ({exc})"
        result.log.append(f"refused: {result.refused_reason}")
        return result
    differs = [
        name
        for name, same in (
            ("changes", canonical_json(fresh["changes"]) == canonical_json(data["changes"])),
            ("policy_sha256", fresh.get("policy_sha256") == data.get("policy_sha256")),
            (
                "desired_state_sha256",
                fresh.get("desired_state_sha256") == data.get("desired_state_sha256"),
            ),
        )
        if not same
    ]
    if differs:
        result.refused_reason = "plan changed"
        result.log.append(
            f"refused: rebuilt from the current world, state and policy, the plan differs in "
            f"{', '.join(differs)}; re-plan and get it approved again"
        )
        return result
    if mode == MOCK_LIVE and write_token is None:
        result.refused_reason = "no write token"
        result.log.append("refused: mock-live needs write tokens")
        return result

    accounts = _Accounts(state.accounts)
    bg_names = _break_glass_names(state, policy)
    clients: dict[str, HttpishClient] = {}
    if mode == MOCK_LIVE and write_token is not None:
        for name, mock in (("okta", mocks.okta), ("google", mocks.google), ("slack", mocks.slack)):
            clients[name] = HttpishClient(
                mock, write_token[name], sleep=sleep, max_retries=MAX_RETRIES
            )

    kept: list[dict[str, Any]] = []
    for c in data["changes"]:
        if c["action"] == "never_touch":
            continue  # a report-only entry for a break-glass account
        if _touches_break_glass(c, policy, bg_names):
            result.dropped_break_glass.append(dict(c))
            result.log.append(
                f"warning: dropped a {c['action']} change for {c['account'].strip()}: "
                "break-glass account"
            )
            continue
        kept.append(c)
    result.manual_tasks = plan_manual_tasks(kept)

    # Okta app name -> every Okta app id with that name (two or more: no add can be made)
    app_ids: dict[str, list[str]] = {}
    if any(c["system"] == "okta" and c["app_id"] and c["action"] == "add" for c in kept):
        reader = HttpishClient(
            mocks.okta, mocks.okta.issue_token("apply-read", {"read"}), sleep=sleep
        )
        try:
            for a in reader.okta_pages("/api/v1/apps", {"limit": 200}):
                ids = app_ids.setdefault(a["name"], [])
                if a["id"] not in ids:
                    ids.append(a["id"])
        except CollectorError as exc:
            result.refused_reason = f"cannot read the Okta apps: {exc}"
            result.log.append(f"refused: {result.refused_reason}")
            return result

    ordered = sorted(enumerate(kept), key=lambda ic: (_step(ic[1]), ic[0]))
    for _, c in ordered:
        _one(c, accounts, clients, app_ids, result, dry=mode != MOCK_LIVE)
    if mode == MOCK_LIVE and result.writes_sent:
        _verify(result, mocks, policy, exports_dir, sleep, before=state, named=kept)
    verb = "would apply" if mode != MOCK_LIVE else "applied"
    result.log.append(
        f"{verb} {len(result.applied)}, already done {len(result.skipped_already_done)}, "
        f"dropped break-glass {len(result.dropped_break_glass)}, "
        f"manual tasks {len(result.manual_tasks)}, failed {len(result.failed)}, "
        f"changed outside the plan {len(result.unexpected)}"
    )
    return result


def _verify(
    result: ApplyResult,
    mocks: Mocks,
    policy: Policy,
    exports_dir: Path | str | None,
    sleep: Callable[[float], None],
    *,
    before: ActualState,
    named: Sequence[Mapping[str, Any]],
) -> None:
    """Read the state again and check each applied change took effect; a change that did not
    (or cannot be checked) moves from `applied` to `failed`. Then report every account that
    changed since `before` although no change in `named` names it (see `_unexpected`). An
    answer the collectors cannot read (a missing field, a wrong type) is a failed read too: the
    writes are made, so the record of them must not be lost to a crash."""
    try:
        after = actual_state(mocks, policy, exports_dir, sleep=sleep)
    except (CollectorError, KeyError, TypeError, ValueError, AttributeError) as exc:
        why = str(exc) if isinstance(exc, CollectorError) else f"{type(exc).__name__} {exc}"
        reason = f"could not read the state to verify: {why}"
        result.failed += [{**c, "error": reason} for c in result.applied]
        result.log.append(f"failed: {reason}")
        result.applied = []
        return
    now = _Accounts(after.accounts)
    kept: list[dict[str, Any]] = []
    for c in result.applied:
        try:
            a = now.find(c["system"], c["app_id"], c["account"])
        except Ambiguous as exc:
            result.failed.append({**c, "error": f"cannot be checked: {exc}"})
            result.log.append(f"failed: {c['action']} {c['account']}: cannot be checked: {exc}")
            continue
        if _took_effect(c, a):
            kept.append(c)
        else:
            result.failed.append({**c, "error": "not in the wanted state after the call"})
            result.log.append(f"failed: {c['action']} {c['account']}: not in the wanted state")
    result.applied = kept
    _unexpected(result, before, after, named)


def _unexpected(
    result: ApplyResult,
    before: ActualState,
    after: ActualState,
    named: Sequence[Mapping[str, Any]],
) -> None:
    """Every account that appeared, went or changed between `before` and `after` and that no
    change in `named` names (same system, app and name, case and spaces ignored; an Okta user's
    change also names their app assignments, whose state follows the user's). Such a change was
    made by a write that reached another account, or by someone else during the apply: either
    way the plan did not ask for it, so it is reported."""
    names = {(c["system"], c["app_id"], _norm(c["account"])) for c in named}

    def is_named(a: Account) -> bool:
        key = (a.system, a.app_id, _norm(a.account))
        return key in names or (a.system == "okta" and (a.system, "", key[2]) in names)

    was, now = collections.Counter(before.accounts), collections.Counter(after.accounts)
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for side, gone_or_new in (("was", was - now), ("now", now - was)):
        for a in gone_or_new:
            if is_named(a):
                continue
            row = rows.setdefault(
                (a.system, a.app_id, a.account, a.source_ref),
                {
                    "system": a.system,
                    "app_id": a.app_id,
                    "account": a.account,
                    "source_ref": a.source_ref,
                    "was": "absent",
                    "now": "absent",
                },
            )
            row[side] = a.state or "unknown"
    for key in sorted(rows):
        row = rows[key]
        result.unexpected.append(row)
        where = f"{row['system']}/{row['app_id']}" if row["app_id"] else row["system"]
        result.log.append(
            f"failed: {row['account']} ({where}) changed during the apply "
            f"({row['was']} -> {row['now']}), but no change in the plan names it"
        )


def _took_effect(c: dict[str, Any], a: Account | None) -> bool:
    system, action = c["system"], c["action"]
    if action == "add":
        return a is not None
    if system == "okta" and c["app_id"]:
        return a is None
    if a is None:
        return False
    if system == "okta":
        wanted = ("suspended", "deprovisioned") if action == "suspend" else ("deprovisioned",)
        return a.state in wanted
    return a.state == ("suspended" if system == "google" else "deprovisioned")


def _ambiguous(c: dict[str, Any], label: str, exc: Ambiguous, result: ApplyResult) -> None:
    result.failed.append({**c, "error": f"{exc}; none was changed"})
    result.log.append(
        f"failed: {label}: {exc}, so it is not clear which one the plan means; none was changed"
    )


def _one(
    c: dict[str, Any],
    accounts: _Accounts,
    clients: dict[str, HttpishClient],
    app_ids: dict[str, list[str]],
    result: ApplyResult,
    *,
    dry: bool,
) -> None:
    system, app_id, action = c["system"], c["app_id"], c["action"]
    label = f"{action} {c['account']} ({system}{'/' + app_id if app_id else ''})"
    if system not in DIRECTORY:
        if action == "flag" and system != "hris":
            result.log.append(f"manual task: {label}")
        elif action == "add":
            result.not_applied.append(dict(c))
            result.manual_tasks.append(
                {
                    "app_id": app_id,
                    "account": c["account"],
                    "task": f"add {c['account']} to {c['app_name'] or app_id}",
                    "action": "add",
                }
            )
            result.log.append(f"manual task: {label}")
        else:
            result.not_applied.append(dict(c))
            result.log.append(f"not applied: {label} (not an API change)")
        return
    if action == "flag":
        result.flagged_for_review.append(dict(c))
        result.log.append(f"review by hand: {label}")
        return
    if action not in ("suspend", "remove", "add"):
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label}")
        return
    if action == "add":
        _assign(c, label, accounts, clients, app_ids, result, dry=dry)
        return
    try:
        current = accounts.find(system, app_id, c["account"])
    except Ambiguous as exc:
        _ambiguous(c, label, exc, result)
        return
    if current is None:
        result.skipped_already_done.append({**c, "why": "already absent"})
        result.log.append(f"already done: {label} (absent)")
        return
    call = _plan_call(c, current)
    if call is None:
        result.skipped_already_done.append({**c, "why": f"already {current.state}"})
        result.log.append(f"already done: {label} ({current.state})")
        _defer_delete(c, result)
        return
    mock_name, method, path, body, new_state = call
    entry = {**c, "call": f"{method} {path}"}
    if dry:
        result.applied.append(entry)
        result.log.append(f"would: {label} via {method} {path}")
    elif not _send(clients[mock_name], system, method, path, body, c, entry, label, result):
        return
    # keep the local view in step with what was (or would be) done
    if current.app_id and system == "okta":
        accounts.remove(current)
    else:
        accounts.replace(current, _with_state(current, new_state))
    _defer_delete(c, result)


def _defer_delete(c: dict[str, Any], result: ApplyResult) -> None:
    if c["system"] == "google" and c["action"] == "remove":
        result.deferred_deletes.append(dict(c))
        result.log.append(f"google: {c['account']} suspended, delete in a later plan")


def _send(
    client: HttpishClient,
    system: str,
    method: str,
    path: str,
    body: Any,
    c: dict[str, Any],
    entry: dict[str, Any],
    label: str,
    result: ApplyResult,
) -> bool:
    """Make one write. True when the change is applied (or already done); False when it failed."""
    result.writes_sent += 1
    try:
        response = client.request(method, path, body=body)
    except CollectorError as exc:
        result.failed.append({**entry, "error": str(exc)})
        result.log.append(f"failed: {label}: {exc}")
        return False
    if response.status == 404 and any(
        s == system and m == method and rx.fullmatch(path) for s, m, rx in _GONE_OK
    ):
        result.skipped_already_done.append({**c, "why": "already gone (404)"})
        result.log.append(f"already done: {label} (404)")
        return False
    if not 200 <= response.status < 300:
        result.failed.append({**entry, "error": f"status {response.status}"})
        result.log.append(f"failed: {label}: status {response.status}")
        return False
    result.applied.append(entry)
    result.log.append(f"applied: {label} via {method} {path}")
    return True


def _assign(
    c: dict[str, Any],
    label: str,
    accounts: _Accounts,
    clients: dict[str, HttpishClient],
    app_ids: dict[str, list[str]],
    result: ApplyResult,
    *,
    dry: bool,
) -> None:
    """A mover's `add` for an SSO app: assign the Okta user to the app (idempotent)."""
    system, app_id = c["system"], c["app_id"]
    if system != "okta" or not app_id:
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label} (only Okta SSO apps can be assigned)")
        return
    try:
        user = accounts.find("okta", "", c["account"])
        assigned = accounts.find("okta", app_id, c["account"])
    except Ambiguous as exc:
        _ambiguous(c, label, exc, result)
        return
    if user is None or user.state == "deprovisioned":
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label} (no active Okta user)")
        return
    if assigned is not None:
        result.skipped_already_done.append({**c, "why": "already assigned"})
        result.log.append(f"already done: {label} (assigned)")
        return
    okta_apps = app_ids.get(app_id, [])
    if not okta_apps:
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label} (no such app in Okta)")
        return
    if len(okta_apps) > 1:
        why = f"{len(okta_apps)} Okta apps are named {app_id}"
        result.failed.append({**c, "error": f"{why}; assigned to none"})
        result.log.append(
            f"failed: {label}: {why}, so it is not clear which one the plan means; assigned to none"
        )
        return
    okta_app = okta_apps[0]
    path = f"/api/v1/apps/{okta_app}/users"
    body = {"id": user.source_ref, "scope": "USER", "profile": {"role": "member"}}
    entry = {**c, "call": f"POST {path}"}
    if dry:
        result.applied.append(entry)
        result.log.append(f"would: {label} via POST {path}")
    elif not _send(clients["okta"], "okta", "POST", path, body, c, entry, label, result):
        return
    accounts.add(
        dataclasses.replace(
            user, app_id=app_id, state="active", source_ref=f"{user.source_ref}@{okta_app}"
        )
    )


def _with_state(a: Account, state: str) -> Account:
    return dataclasses.replace(a, state=state)  # type: ignore[arg-type]


def _plan_call(c: dict[str, Any], a: Account) -> tuple[str, str, str, Any, str] | None:
    """(mock, method, path, body, new state) for a change, or None when the account is
    already in the wanted state. Suspend before remove: Okta and Google users are suspended
    for `suspend`; Google `remove` is a suspension (deletion is for a later plan)."""
    system, action = c["system"], c["action"]
    if system == "okta" and a.app_id:
        user_id, _, okta_app = a.source_ref.partition("@")
        return ("okta", "DELETE", f"/api/v1/apps/{okta_app}/users/{user_id}", None, "deprovisioned")
    if system == "okta":
        if action == "suspend":
            if a.state in ("suspended", "deprovisioned"):
                return None
            return (
                "okta",
                "POST",
                f"/api/v1/users/{a.source_ref}/lifecycle/suspend",
                None,
                "suspended",
            )
        if a.state == "deprovisioned":
            return None
        return (
            "okta",
            "POST",
            f"/api/v1/users/{a.source_ref}/lifecycle/deactivate",
            None,
            "deprovisioned",
        )
    if system == "google":
        if a.state == "suspended":
            return None
        return (
            "google",
            "PATCH",
            f"/admin/directory/v1/users/{a.source_ref}",
            {"suspended": True},
            "suspended",
        )
    if a.state == "deprovisioned":  # slack: remove and suspend both mean active=false
        return None
    body = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
    return ("slack", "PATCH", f"/scim/v2/Users/{a.source_ref}", body, "deprovisioned")


def confirm_manual_tasks(
    tasks: list[dict[str, str]],
    next_exports_dir: Path | str,
    policy: Policy | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(done, pending), read with the same parser as the plan. A removal task is done when its
    row is gone from the next export; an "add" task (`"action": "add"`) when the row is
    there. A file confirms a task only when its header was found and it has no unreadable
    lines: a truncated, empty or damaged export is not evidence, so the task stays pending
    (with a `reason`), as it does when the app has no export file at all."""
    policy = policy or load_policy()
    parsed = parse_exports(Path(next_exports_dir), policy)
    present = {(a.system, _norm(a.account)) for a in parsed.accounts}
    reports: dict[str, list[Any]] = {}
    for f in parsed.files:
        reports.setdefault(f.app_id, []).append(f)
    done, pending = [], []
    for t in tasks:
        files = reports.get(t["app_id"], [])
        reason = ""
        if not files:
            reason = "no export file for this app"
        elif any(f.header_line <= 0 for f in files):
            reason = "the export has no header row"
        elif any(f.unreadable for f in files):
            reason = f"the export has {sum(f.unreadable for f in files)} unreadable lines"
        else:
            here = (t["app_id"], _norm(t["account"])) in present
            if here == (t.get("action") == "add"):
                done.append(t)
                continue
            reason = "the row is still in the export" if here else "the row is not in the export"
        pending.append({**t, "reason": reason})
    return done, pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.apply")
    parser.add_argument("--plan", type=Path, required=True, help="plan.json")
    parser.add_argument("--world", type=Path, required=True, help="a world directory (the mocks)")
    parser.add_argument("--approvals", type=int, required=True, help="approvals the plan has")
    parser.add_argument("--policy-dir", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None, help="write the apply log (JSON) here")
    args = parser.parse_args(argv)
    policy = load_policy(args.policy_dir)
    mode = normalise_mode(os.environ.get(MODE_ENV))
    if json.loads(args.plan.read_text(encoding="utf-8")).get("part") == "held":
        print(
            "refused: the held plan is applied only when its pull request is merged with two "
            "code-owner approvals; that path needs the owner's GitHub App and is not built"
        )
        return 1
    root = args.world
    mocks = Mocks(
        OktaMock.from_files(root), GoogleMock.from_files(root), SlackMock.from_files(root)
    )
    for mock in (mocks.okta, mocks.google, mocks.slack):
        mock.fault_rate = 0.0
    result = apply_plan(
        args.plan,
        mocks,
        mode=mode,
        approvals=args.approvals,
        policy=policy,
        write_token=issue_write_tokens(mocks) if mode == MOCK_LIVE else None,
        world_dir=root,
        policy_dir=args.policy_dir,
    )
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(
            json.dumps(result.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    print(f"mode: {result.mode}")
    if result.refused_reason:
        print(f"refused: {result.refused_reason}")
    print(
        f"applied: {len(result.applied)}  already done: {len(result.skipped_already_done)}  "
        f"dropped break-glass: {len(result.dropped_break_glass)}  "
        f"manual tasks: {len(result.manual_tasks)}  failed: {len(result.failed)}  "
        f"changed outside the plan: {len(result.unexpected)}  "
        f"held (never applied): {result.held_not_applied}"
    )
    return 1 if result.refused_reason or result.failed or result.unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
