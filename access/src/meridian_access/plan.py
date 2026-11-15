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
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from meridian_access.collect import ActualState, actual_state
from meridian_access.desired import desired_state, load_hris
from meridian_access.diff import DIRECTORY, REMOVING, Change, build_changes
from meridian_access.match import match_accounts
from meridian_access.policy.load import Policy, default_policy_dir, load_policy

PLAN_VERSION = 1
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
    """Build the plan for one world directory (hris.json, okta.json, google.json,
    slack_scim.json and exports/). The answer key is never read."""
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
    by_type = collections.Counter(c["type"] for c in changes)
    by_action = collections.Counter(c["action"] for c in changes)
    removals = sum(1 for c in changes if c["action"] in REMOVING)
    n = policy.settings.blast_radius_n
    needs_more = removals > n or any(c["action"] == "block_two_approvers" for c in changes)
    settings = policy.settings
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
        "counts": {
            "total": len(changes),
            "by_type": dict(sorted(by_type.items())),
            "by_action": dict(sorted(by_action.items())),
            "removals_and_suspensions": removals,
            "held": len(held),
            "warnings": len(result.warnings),
            "manual_tasks": len(tasks),
        },
        "approvals_required": settings.approvals_over_n
        if needs_more
        else settings.approvals_default,
        "manual_tasks": tasks,
    }
    return Plan(data)


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


def render_markdown(data: dict[str, Any]) -> str:
    counts = data["counts"]
    out = [f"# Access plan for {data['as_of']}", ""]
    out += [
        f"{counts['total']} changes, {data['approvals_required']} approval(s) required, "
        f"{counts['held']} held, {counts['warnings']} warnings, "
        f"{counts['manual_tasks']} manual tasks.",
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
    out += ["", "## Held (not applied)", ""]
    held = data["held"]
    if held:
        out += [f"- {describe(c)}" for c in held]
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


def write_outputs(
    plan: Plan, out: Path, pr_url: str, live: bool, timings: dict[str, float], started: float
) -> None:
    """Write the five output files. Everything is made in a temporary directory first and
    moved into `out` only when all of it exists, so a failure never leaves a mix of new and
    stale files."""
    from meridian_access import slack_mock, summary

    out.mkdir(parents=True, exist_ok=True)
    client = None
    if live:
        from meridian_access.ai.client import ClaudeClient
        from meridian_access.ai.pricing import model_id

        client = ClaudeClient(model_id(os.environ.get("ACCESS_SUMMARY_MODEL", "sonnet")))
    with tempfile.TemporaryDirectory(prefix=".plan-", dir=out) as tmp_name:
        tmp = Path(tmp_name)
        (tmp / "plan.json").write_text(plan.json(), encoding="utf-8")
        (tmp / "PLAN.md").write_text(plan.markdown(), encoding="utf-8")
        (tmp / "summary.md").write_text(summary.summary_markdown(plan, client), encoding="utf-8")
        slack_mock.write_message(slack_mock.build_message(plan, pr_url), tmp / "slack-message.json")
        elapsed = round(time.perf_counter() - started, 3)
        timing = {"elapsed_seconds": elapsed, "steps": timings}
        (tmp / "timing.json").write_text(canonical_json(timing), encoding="utf-8")
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
    plan = build_plan(
        args.world, load_policy(args.policy_dir), timings=timings, policy_dir=args.policy_dir
    )
    live = os.environ.get("ACCESS_AI_LIVE") == "1"
    write_outputs(plan, args.out, args.pr_url, live, timings, started)
    counts = plan.counts
    print(f"changes: {counts['total']}  held: {counts['held']}  warnings: {counts['warnings']}")
    for name, n in counts["by_type"].items():
        print(f"  {name}: {n}")
    for name, n in counts["by_action"].items():
        print(f"  action {name}: {n}")
    print(f"approvals required: {plan.data['approvals_required']}")
    for step, seconds in timings.items():
        print(f"  {step}: {seconds}s")
    print(f"elapsed: {round(time.perf_counter() - started, 3)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
