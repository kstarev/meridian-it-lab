"""The plan: HRIS + policy -> desired state; mocks -> actual state; match; diff; safety.

`build_plan` is deterministic: the same inputs give the same plan.json and PLAN.md, byte for
byte. It never reads the answer key. Timing goes in a separate timing.json so plan.json stays
the same from run to run.

    python -m meridian_access.plan --world out/access/seed-6108 --out out/access/plan
"""

import argparse
import collections
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from meridian_access.collect import ActualState, actual_state
from meridian_access.desired import desired_state, load_hris
from meridian_access.diff import DIRECTORY, REMOVING, TYPE_ORDER, Change, build_changes
from meridian_access.limits import NO_DATE, bucket_key, buckets, over_limit
from meridian_access.match import match_accounts
from meridian_access.policy.load import Policy, default_policy_dir, load_policy

PLAN_VERSION = 2
POLICY_FILES = ("apps", "roles", "title_map", "exceptions", "break_glass", "settings")
SYSTEM_NAMES = {"okta": "Okta", "google": "Google", "slack": "Slack", "hris": "HRIS"}
SECTIONS = (
    ("hris_mass_termination", "Blocked: bulk HR change"),
    ("hr_data_error", "HR data errors"),
    ("leaver_access_left", "Leavers who still have access"),
    ("orphaned_account", "Orphaned accounts"),
    ("contractor_past_end", "Contractors past their end date"),
    ("leave_of_absence", "People on leave"),
    ("mover_stale_access", "Movers"),
    ("rehire", "Rehired people"),
    ("drift", "Drift (changed outside the process)"),
    ("break_glass", "Break-glass accounts (never touched)"),
)


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def policy_sha256(policy_dir: Path | None = None) -> str:
    """Hash of the policy files that shape a plan (apps, roles, title map, exceptions,
    break-glass list, settings and the suggested/reviewed map)."""
    root = policy_dir or default_policy_dir()
    h = hashlib.sha256()
    for name in (*POLICY_FILES, "suggested_maps"):
        path = root / f"{name}.yaml"
        h.update(name.encode() + b"\0")
        h.update(path.read_bytes() if path.is_file() else b"")
        h.update(b"\0")
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class Plan:
    data: dict[str, Any]

    @property
    def changes(self) -> list[dict[str, Any]]:
        return self.data["changes"]

    @property
    def counts(self) -> dict[str, Any]:
        return self.data["counts"]

    def json(self) -> str:
        return canonical_json(self.data)

    def markdown(self) -> str:
        return render_markdown(self.data)


def change_dict(c: Change, policy: Policy) -> dict[str, Any]:
    app = policy.apps.get(c.app_id or c.system)
    return {
        "type": c.type,
        "employee_id": c.employee_id,
        "account": c.account,
        "system": c.system,
        "app_id": c.app_id,
        "app_name": app.name if app else "",
        "action": c.action,
        "reason": c.reason,
        "evidence": list(c.evidence),
        "cause_date": c.cause_date,
        "hold": c.hold,
    }


def _is_export_row(c: dict[str, Any]) -> bool:
    return c["system"] not in (*DIRECTORY, "hris")


def manual_tasks(changes: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    tasks = []
    for c in changes:
        if c["action"] == "flag" and _is_export_row(c):
            name = c["app_name"] or c["app_id"]
            tasks.append(
                {
                    "app_id": c["app_id"],
                    "account": c["account"],
                    "task": f"Remove {c['account']} from {name}, confirm in next export",
                }
            )
    return sorted(tasks, key=lambda t: (t["app_id"], t["account"].casefold(), t["task"]))


def _counts(
    changes: Sequence[dict[str, Any]],
    held: Sequence[Any],
    warnings: Sequence[Any],
    tasks: Sequence[Any],
) -> dict[str, Any]:
    by_type = collections.Counter(c["type"] for c in changes)
    by_action = collections.Counter(c["action"] for c in changes)
    return {
        "total": len(changes),
        "by_type": dict(sorted(by_type.items())),
        "by_action": dict(sorted(by_action.items())),
        "removals_and_suspensions": sum(1 for c in changes if c["action"] in REMOVING),
        "held": len(held),
        "held_bad_data": sum(1 for c in held if c.get("hold") == "bad_data"),
        "warnings": len(warnings),
        "manual_tasks": len(tasks),
    }


def build_plan(
    world_dir: Path | str,
    policy: Policy,
    as_of: date | None = None,
    *,
    policy_dir: Path | None = None,
    map_entries: Sequence[Mapping[str, Any]] | None = None,
    timings: dict[str, float] | None = None,
    state: ActualState | None = None,
) -> Plan:
    """Build the combined plan for one world directory (hris.json, okta.json, google.json,
    slack_scim.json and exports/). The answer key is never read. It holds every change and
    every held change; `split_plan` turns it into the main plan and the held plan."""
    from meridian_access.ai.maps import load_entries

    root = Path(world_dir)
    clock = time.perf_counter
    marks: dict[str, float] = timings if timings is not None else {}

    def lap(name: str, since: float) -> float:
        now = clock()
        marks[name] = round(now - since, 3)
        return now

    t = clock()
    records, events = load_hris(root / "hris.json")
    desired = desired_state(records, events, policy, as_of)
    t = lap("desired_state", t)
    actual = state if state is not None else actual_state(root, policy)
    t = lap("collect", t)
    matches = match_accounts(actual.accounts, records, policy)
    t = lap("match", t)
    entries = list(map_entries) if map_entries is not None else load_entries()
    result = build_changes(
        desired,
        actual,
        matches,
        policy,
        records=records,
        events=events,
        map_entries=entries,
    )
    lap("diff", t)
    changes = [change_dict(c, policy) for c in result.changes]
    held = [change_dict(c, policy) for c in result.held]
    from meridian_access.approvals import required_for

    tasks = manual_tasks(changes)
    data: dict[str, Any] = {
        "version": PLAN_VERSION,
        "as_of": desired.as_of.isoformat(),
        "actual_state_sha256": actual.digest(),
        "desired_state_sha256": desired.digest(),
        "policy_sha256": policy_sha256(policy_dir),
        "changes": changes,
        "held": held,
        "warnings": list(result.warnings),
        "suggested_map_entries": [dict(e) for e in result.suggested_map_entries],
        "counts": _counts(changes, held, result.warnings, tasks),
        "approvals_required": required_for(changes, policy),
        "manual_tasks": tasks,
    }
    return Plan(data)


# ------------------------------------------------------------------------------ the split


def _change_key(c: dict[str, Any]) -> tuple[Any, ...]:
    order = TYPE_ORDER.index(c["type"]) if c["type"] in TYPE_ORDER else len(TYPE_ORDER)
    return (
        order,
        c["employee_id"],
        c["system"],
        c["app_id"],
        c["account"].strip().casefold(),
        c["action"],
    )


def _identity(c: dict[str, Any]) -> str:
    """A change without the fields the split rewrites (reason suffix, hold), for the partition
    check."""
    base = {k: v for k, v in c.items() if k not in ("hold", "reason")}
    return canonical_json(base)


def _part(
    data: dict[str, Any],
    part: str,
    changes: list[dict[str, Any]],
    held: list[dict[str, Any]],
    approvals: int,
) -> dict[str, Any]:
    tasks = manual_tasks(changes)
    return {
        **{k: v for k, v in data.items() if k not in ("changes", "held", "counts", "manual_tasks")},
        "part": part,
        "changes": changes,
        "held": held,
        "counts": _counts(changes, held, data["warnings"], tasks),
        "approvals_required": approvals,
        "manual_tasks": tasks,
    }


def split_plan(plan: Plan, policy: Policy) -> tuple[Plan, Plan | None]:
    """Split the combined plan into (main, held).

    The 5-people / 50-removals limit is counted per cause-day bucket (see `limits`). Every
    change of an over-limit bucket goes to the held plan, which needs `approvals_over_n`
    approvals, except break-glass and HR-data-error entries (they stay in main). Every change
    held by a bulk import and every `block_two_approvers` entry also goes to the held plan. The
    main plan needs `approvals_default`. Changes held for bad data (HR data error, notice
    period, duplicate id) are never applied by either plan: they stay in main's `held` list.
    Main's `held` list also names every change moved to the held plan (they are applied only
    when the held plan is, after two approvals); main's PLAN.md and Slack message do not list
    them one by one: they show the buckets and `held_plan.by_type`. The held plan applies its
    changes (bulk-held ones included, their `hold` cleared) after two approvals. Raises
    ValueError when an invariant breaks."""
    from meridian_access.approvals import required_for

    data = plan.data
    settings = policy.settings
    combined = list(data["changes"])
    bulk = [c for c in data["held"] if c.get("hold") == "bulk"]
    bad = [c for c in data["held"] if c.get("hold") == "bad_data"]
    if len(bulk) + len(bad) != len(data["held"]):
        raise ValueError("a held change has no known hold kind")
    over = {k: b for k, b in buckets([*combined, *bulk]).items() if over_limit(b, settings)}
    stay: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    moved_for_main: list[dict[str, Any]] = []
    for c in combined:
        if c["action"] == "block_two_approvers":
            moved.append(c)
            moved_for_main.append(c)
        elif c["type"] in ("break_glass", "hr_data_error"):
            stay.append(c)
        elif over and bucket_key(c) in over:
            day = "no date" if bucket_key(c) == NO_DATE else bucket_key(c)
            moved.append(c)
            moved_for_main.append(
                {**c, "reason": f"{c['reason']}; in the held plan: {day} over the limit"}
            )
        else:
            stay.append(c)
    held_changes = sorted([*moved, *({**c, "hold": ""} for c in bulk)], key=_change_key)
    expected = sorted(_identity(c) for c in [*combined, *bulk])
    if sorted(_identity(c) for c in [*stay, *held_changes]) != expected:
        raise ValueError("the split lost or duplicated a change")
    main_held = sorted([*bad, *moved_for_main, *bulk], key=_change_key)
    main_approvals = required_for(stay, policy)
    if main_approvals != settings.approvals_default:
        raise ValueError(
            f"the main plan needs {main_approvals} approvals, expected {settings.approvals_default}"
        )
    if not held_changes:
        return Plan(
            _part(data, "main", stay, main_held, main_approvals) | {"held_plan": None}
        ), None
    held_approvals = required_for(held_changes, policy)
    if held_approvals != settings.approvals_over_n:
        raise ValueError(
            f"the held plan needs {held_approvals} approvals, expected {settings.approvals_over_n}"
        )
    bucket_list = [
        {"day": k, "people": len(b.people), "removals": b.removals} for k, b in sorted(over.items())
    ]
    main = _part(data, "main", stay, main_held, main_approvals)
    main["held_plan"] = {
        "buckets": bucket_list,
        "changes": len(held_changes),
        "by_type": dict(sorted(collections.Counter(c["type"] for c in held_changes).items())),
        "manual_tasks": len(manual_tasks(held_changes)),
        "approvals_required": held_approvals,
    }
    held_data = _part(data, "held", held_changes, [], held_approvals)
    held_data["held_plan"] = None
    held_data["buckets"] = bucket_list
    return Plan(main), Plan(held_data)


# ------------------------------------------------------------------------------ PLAN.md


def describe(c: dict[str, Any]) -> str:
    """One plain-English line for a change."""
    acct = c["account"]
    app = c["app_name"] or c["app_id"]
    system = SYSTEM_NAMES.get(c["system"], c["system"])
    action = c["action"]
    in_app = bool(c["app_id"]) or c["system"] not in (*DIRECTORY, "hris")
    if action == "block_two_approvers":
        line = "Block the bulk HR change until two approvers have reviewed it"
    elif action == "never_touch":
        line = f"Leave {acct} alone in {system} (break-glass account)"
    elif c["system"] == "hris":
        line = f"Flag HR record {acct} for correction"
    elif action == "remove":
        line = f"Remove {acct} from {app}" if in_app else f"Remove the {system} account of {acct}"
    elif action == "suspend":
        line = f"Suspend the {system} account of {acct}"
    elif action == "add":
        line = f"Add {acct} to {app}"
    elif action == "flag":
        if c["system"] in DIRECTORY:
            line = f"Review {acct} in {app} (changed by hand)"
        else:
            line = f"Flag {acct} in {app} (remove by hand, no API)"
    else:
        line = f"{action} {acct}"
    return f"{line} ({c['reason']})" if c["reason"] else line


def _bucket_lines(buckets_: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"- {'no date' if b['day'] == NO_DATE else b['day']}: {b['people']} people, "
        f"{b['removals']} removals and suspensions"
        for b in buckets_
    ]


def _type_lines(by_type: Mapping[str, int]) -> list[str]:
    titles = dict(SECTIONS)
    order = [k for k, _ in SECTIONS if by_type.get(k)] + sorted(
        k for k in by_type if k not in titles
    )
    return [f"- {titles.get(k, k)}: {by_type[k]}" for k in order]


def render_markdown(data: dict[str, Any]) -> str:
    counts = data["counts"]
    is_held = data.get("part") == "held"
    if is_held:
        out = ["# Held access plan: needs two approvers", ""]
        out += [
            f"This is the held part of the access plan for {data['as_of']}, in its own pull "
            "request: the part of the plan that needs two code-owner approvals before it is "
            "applied. It holds every bulk-import change and every change of a day (or the "
            "no-date bucket) over the limit.",
            "",
        ]
        if data.get("buckets"):
            out += ["Buckets over the limit:", "", *_bucket_lines(data["buckets"]), ""]
        else:
            out += ["No bucket is over the limit; the plan holds a bulk import.", ""]
    else:
        out = [f"# Access plan for {data['as_of']}", ""]
    held_plan = data.get("held_plan")
    if held_plan:
        out += ["## Held plan (needs two approvers)", ""]
        out += [
            f"{held_plan['changes']} changes are in the held plan (applied only after two "
            "approvals), a separate pull request. It holds:",
            "",
            *(_bucket_lines(held_plan["buckets"]) or ["- a bulk import"]),
            "",
            "The held plan has these changes (they are not listed one by one here):",
            "",
            *_type_lines(held_plan["by_type"]),
            f"- Manual tasks: {held_plan['manual_tasks']}",
            "",
        ]
    bad = [c for c in data["held"] if c.get("hold") == "bad_data"]
    out += [
        f"{counts['total']} changes in this plan, {data['approvals_required']} approval(s) "
        f"required, {counts['held_bad_data']} held for bad HR data and never applied, "
        f"{counts['warnings']} warnings, {counts['manual_tasks']} manual tasks.",
        "",
        "| Type | Changes |",
        "|---|---|",
    ]
    for key, title in SECTIONS:
        out.append(f"| {title} | {counts['by_type'].get(key, 0)} |")
    out += ["", "| Action | Changes |", "|---|---|"]
    for action, n in counts["by_action"].items():
        out.append(f"| {action} | {n} |")
    out += ["", f"Plan built from actual state `{data['actual_state_sha256'][:12]}`.", ""]
    for key, title in SECTIONS:
        rows = [c for c in data["changes"] if c["type"] == key]
        if not rows:
            continue
        out += [f"## {title}", ""]
        out += [f"- {describe(c)}" for c in rows]
        out.append("")
    out += ["## Warnings", ""]
    out += [f"- {w}" for w in data["warnings"]] or ["None."]
    out += ["", "## Held for bad HR data (never applied)", ""]
    if bad:
        out += [f"- {describe(c)}" for c in bad]
    else:
        out.append("None.")
    out += ["", "## Manual tasks", ""]
    out += [f"- {t['task']}" for t in data["manual_tasks"]] or ["None."]
    out += ["", "## Suggested map entries (need review before anything uses them)", ""]
    suggestions = data["suggested_map_entries"]
    out += [f"- {e.get('kind')}: {e.get('key')} -> {e.get('value')}" for e in suggestions] or [
        "None."
    ]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------- CLI


OUTPUT_FILES = ("plan.json", "PLAN.md", "summary.md", "slack-message.json", "timing.json")
HELD_FILES = ("plan.json", "PLAN.md", "summary.md")


def write_outputs(
    plan: Plan,
    out: Path,
    pr_url: str,
    live: bool,
    timings: dict[str, float],
    started: float,
    held: Plan | None = None,
) -> None:
    """Write the five output files of the main plan, and the held plan's three files into
    `out/held/` (a stale `out/held/` is removed when there is no held plan). Everything is made
    in a temporary directory first and moved into `out` only when all of it exists. Then
    `held/` is replaced (or removed) FIRST and the main files after it. A crash between the two
    steps can leave a new (or removed) `held/` next to the previous main files: the two parts
    then disagree, and apply refuses the older one as stale or changed, so the plan has to be
    built again. Replacing main first could leave an old held plan next to a new main plan."""
    from meridian_access import slack_mock, summary

    out.mkdir(parents=True, exist_ok=True)
    client = None
    if live:
        from meridian_access.ai.client import ClaudeClient
        from meridian_access.ai.pricing import CHOSEN_MODEL, model_id

        client = ClaudeClient(model_id(os.environ.get("ACCESS_SUMMARY_MODEL", CHOSEN_MODEL)))
    with tempfile.TemporaryDirectory(prefix=".plan-", dir=out) as tmp_name:
        tmp = Path(tmp_name)
        (tmp / "plan.json").write_text(plan.json(), encoding="utf-8")
        (tmp / "PLAN.md").write_text(plan.markdown(), encoding="utf-8")
        (tmp / "summary.md").write_text(summary.summary_markdown(plan, client), encoding="utf-8")
        slack_mock.write_message(slack_mock.build_message(plan, pr_url), tmp / "slack-message.json")
        elapsed = round(time.perf_counter() - started, 3)
        timing = {"elapsed_seconds": elapsed, "steps": timings}
        (tmp / "timing.json").write_text(canonical_json(timing), encoding="utf-8")
        if held is not None:
            (tmp / "held").mkdir()
            (tmp / "held" / "plan.json").write_text(held.json(), encoding="utf-8")
            (tmp / "held" / "PLAN.md").write_text(held.markdown(), encoding="utf-8")
            (tmp / "held" / "summary.md").write_text(
                summary.summary_markdown(held, client), encoding="utf-8"
            )
        target = out / "held"
        if target.exists():
            shutil.rmtree(target)
        if held is not None:
            os.replace(tmp / "held", target)
        for name in OUTPUT_FILES:
            os.replace(tmp / name, out / name)


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(prog="python -m meridian_access.plan")
    parser.add_argument("--world", type=Path, required=True, help="a world directory")
    parser.add_argument("--out", type=Path, required=True, help="where the plan files go")
    parser.add_argument("--policy-dir", type=Path, default=None, help="default: access/policy")
    parser.add_argument("--pr-url", default="https://example.com/plan-pull-request")
    args = parser.parse_args(argv)
    timings: dict[str, float] = {}
    policy = load_policy(args.policy_dir)
    combined = build_plan(args.world, policy, timings=timings, policy_dir=args.policy_dir)
    plan, held_plan = split_plan(combined, policy)
    live = os.environ.get("ACCESS_AI_LIVE") == "1"
    write_outputs(plan, args.out, args.pr_url, live, timings, started, held_plan)
    for label, part in (("main plan", plan), ("held plan", held_plan)):
        if part is None:
            print("held plan: none")
            continue
        counts = part.counts
        print(
            f"{label}: changes: {counts['total']}  held: {counts['held']}  "
            f"warnings: {counts['warnings']}"
        )
        for name, n in counts["by_type"].items():
            print(f"  {name}: {n}")
        for name, n in counts["by_action"].items():
            print(f"  action {name}: {n}")
        print(f"  approvals required: {part.data['approvals_required']}")
    for step, seconds in timings.items():
        print(f"  {step}: {seconds}s")
    print(f"elapsed: {round(time.perf_counter() - started, 3)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
