"""Apply a plan, safely, to the in-process mocks (AAC-007).

There is no mode for real systems. `mode` is `dry-run` (the default: say what would be done,
change nothing) or `mock-live` (change the in-process mocks). Any other value is a dry run.
Apply never talks to anything but the mocks it is given.

Before any change it re-checks what the pull request check already checked: the plan has
enough approvals (the requirement is recomputed from the changes and the policy, never taken
from the plan file alone) and is not stale (the actual state still has the hash the plan was
built from). It then rebuilds the plan from the current world, state and policy and refuses
when the changes, the policy hash or the desired-state hash differ from the approved plan, so
what was approved is what gets applied. Held changes are never applied. A final break-glass
guard drops any change that names a break-glass account, whatever the diff did. Every change
checks the current state first, so a second run does nothing. Suspension comes before removal
where the API allows it.
After a mock-live apply the state is read again and every applied change is checked to have
taken effect; one that did not is reported as failed.

    ACCESS_APPLY_MODE=mock-live python -m meridian_access.apply --plan plan.json \\
        --world out/access/seed-6108 --approvals 2
"""

import argparse
import dataclasses
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meridian_access.approvals import required_for
from meridian_access.collect import ActualState, HttpishClient, Mocks, actual_state
from meridian_access.exports import parse_exports
from meridian_access.mocks import GoogleMock, OktaMock, SlackMock, Token
from meridian_access.models import Account, CollectorError
from meridian_access.plan import Plan, build_plan, canonical_json
from meridian_access.plan import manual_tasks as plan_manual_tasks
from meridian_access.policy.load import Policy, load_policy
from meridian_dataset.access.model import DOMAIN

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
            "log": self.log,
        }


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
    rebuild-from-the-world step, for tests that apply a hand-made plan. Dry run unless `mode`
    is exactly `mock-live`."""
    mode = normalise_mode(mode)
    plan = _load_plan(plan)
    data = plan.data
    result = ApplyResult(mode=mode)
    result.log.append(f"mode: {mode}")
    result.held_not_applied = len(data.get("held", []))
    if result.held_not_applied:
        result.log.append(f"{result.held_not_applied} held changes are never applied")
    if exports_dir is None and world_dir is not None:
        exports_dir = Path(world_dir) / "exports"

    # what the plan file says is a floor, never the answer: recompute from its own changes
    need = max(int(data["approvals_required"]), required_for(data["changes"], policy))
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

        def rebuild(st: ActualState) -> Plan:
            return build_plan(root, policy, policy_dir=policy_dir, state=st)

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

    index: dict[tuple[str, str, str], Account] = {}
    for a in state.accounts:
        index.setdefault((a.system, a.app_id, _norm(a.account)), a)
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

    app_ids: dict[str, str] = {}
    if any(c["system"] == "okta" and c["app_id"] and c["action"] == "add" for c in kept):
        reader = HttpishClient(
            mocks.okta, mocks.okta.issue_token("apply-read", {"read"}), sleep=sleep
        )
        try:
            app_ids = {
                a["name"]: a["id"] for a in reader.okta_pages("/api/v1/apps", {"limit": 200})
            }
        except CollectorError as exc:
            result.refused_reason = f"cannot read the Okta apps: {exc}"
            result.log.append(f"refused: {result.refused_reason}")
            return result

    ordered = sorted(enumerate(kept), key=lambda ic: (_step(ic[1]), ic[0]))
    for _, c in ordered:
        _one(c, index, clients, app_ids, result, dry=mode != MOCK_LIVE)
    if mode == MOCK_LIVE and result.applied:
        _verify(result, mocks, policy, exports_dir, sleep)
    verb = "would apply" if mode != MOCK_LIVE else "applied"
    result.log.append(
        f"{verb} {len(result.applied)}, already done {len(result.skipped_already_done)}, "
        f"dropped break-glass {len(result.dropped_break_glass)}, "
        f"manual tasks {len(result.manual_tasks)}, failed {len(result.failed)}"
    )
    return result


def _verify(
    result: ApplyResult,
    mocks: Mocks,
    policy: Policy,
    exports_dir: Path | str | None,
    sleep: Callable[[float], None],
) -> None:
    """Read the state again and check each applied change took effect; a change that did not
    (or cannot be checked) moves from `applied` to `failed`."""
    try:
        after = actual_state(mocks, policy, exports_dir, sleep=sleep)
    except CollectorError as exc:
        reason = f"could not read the state to verify: {exc}"
        result.failed += [{**c, "error": reason} for c in result.applied]
        result.log.append(f"failed: {reason}")
        result.applied = []
        return
    now: dict[tuple[str, str, str], Account] = {}
    for a in after.accounts:
        now.setdefault((a.system, a.app_id, _norm(a.account)), a)
    kept: list[dict[str, Any]] = []
    for c in result.applied:
        a = now.get((c["system"], c["app_id"], _norm(c["account"])))
        if _took_effect(c, a):
            kept.append(c)
        else:
            result.failed.append({**c, "error": "not in the wanted state after the call"})
            result.log.append(f"failed: {c['action']} {c['account']}: not in the wanted state")
    result.applied = kept


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


def _one(
    c: dict[str, Any],
    index: dict[tuple[str, str, str], Account],
    clients: dict[str, HttpishClient],
    app_ids: dict[str, str],
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
        _assign(c, label, index, clients, app_ids, result, dry=dry)
        return
    current = index.get((system, app_id, _norm(c["account"])))
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
        index.pop((system, app_id, _norm(c["account"])), None)
    else:
        index[(system, app_id, _norm(c["account"]))] = _with_state(current, new_state)
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
    index: dict[tuple[str, str, str], Account],
    clients: dict[str, HttpishClient],
    app_ids: dict[str, str],
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
    user = index.get(("okta", "", _norm(c["account"])))
    if user is None or user.state == "deprovisioned":
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label} (no active Okta user)")
        return
    if ("okta", app_id, _norm(c["account"])) in index:
        result.skipped_already_done.append({**c, "why": "already assigned"})
        result.log.append(f"already done: {label} (assigned)")
        return
    okta_app = app_ids.get(app_id)
    if okta_app is None:
        result.not_applied.append(dict(c))
        result.log.append(f"not applied: {label} (no such app in Okta)")
        return
    path = f"/api/v1/apps/{okta_app}/users"
    body = {"id": user.source_ref, "scope": "USER", "profile": {"role": "member"}}
    entry = {**c, "call": f"POST {path}"}
    if dry:
        result.applied.append(entry)
        result.log.append(f"would: {label} via POST {path}")
    elif not _send(clients["okta"], "okta", "POST", path, body, c, entry, label, result):
        return
    index[("okta", app_id, _norm(c["account"]))] = dataclasses.replace(
        user, app_id=app_id, state="active", source_ref=f"{user.source_ref}@{okta_app}"
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
        f"held (never applied): {result.held_not_applied}"
    )
    return 1 if result.refused_reason or result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
