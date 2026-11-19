"""A leaver drill: a leaver's full deprovisioning plan, timed from the HRIS change (milestone 5).

    python -m meridian_access.leaver_drill --world out/access/seed-6108 \\
        --out out/access/leaver-drill

It copies the world into `--out`, picks one current employee who has an Okta, a Google and a
Slack account and no HR event this month (the first by employee id), and writes the HR change
that makes them a leaver: status terminated, an end date the day before the reference date,
and a terminate event on that day. The clock starts once the changed hris.json is written and
stops once the plan files are written, with the same steps as `python -m meridian_access.plan`
(read HRIS, collect, match, diff, split, write). Then, outside the timing, it checks that the
plan is the leaver's full deprovisioning: every account matched to them gets a removal, a
suspension or a manual task, in the main plan or the held plan, and that none of them had one
in a plan built from the same world just before the change (so the change is what put them
there).

It writes `leaver-drill.json` into `--out` and exits 1 when the check fails or the time is
`LIMIT_SECONDS` or more. The world given is never changed; only its copy is. Tuning world only:
the plan workflow runs it after the nightly plan.
"""

import argparse
import datetime
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from meridian_access.collect import actual_state
from meridian_access.desired import load_hris
from meridian_access.diff import DIRECTORY, REMOVING
from meridian_access.match import match_accounts
from meridian_access.plan import build_plan, canonical_json, split_plan, write_outputs
from meridian_access.policy.load import Policy, load_policy

LIMIT_SECONDS = 300.0
DEPROVISIONING = (*REMOVING, "flag")
RESULT_FILE = "leaver-drill.json"


def _norm(text: str) -> str:
    return text.strip().casefold()


def pick_leaver(world_dir: Path, policy: Policy) -> str:
    """The first current employee (by id) with no HR event this month and an account in each
    of Okta, Google and Slack matched to them by the rules."""
    records, events = load_hris(world_dir / "hris.json")
    busy = {e.employee_id for e in events}
    state = actual_state(world_dir, policy)
    matches = match_accounts(state.accounts, records, policy)
    systems: dict[str, set[str]] = {}
    for (system, app_id, _), m in matches.matches.items():
        if m.employee_id and not app_id and system in DIRECTORY:
            systems.setdefault(m.employee_id, set()).add(system)
    for r in sorted(records, key=lambda r: r.employee_id):
        if (
            r.status == "active"
            and r.employment_type == "employee"
            and not r.end_date
            and r.employee_id not in busy
            and systems.get(r.employee_id) == set(DIRECTORY)
        ):
            return r.employee_id
    raise ValueError("the world has no current employee with all three accounts to drill")


def write_leaver(world_dir: Path, employee_id: str, policy: Policy) -> str:
    """Make `employee_id` a leaver in `world_dir/hris.json`; return the end date."""
    path = world_dir / "hris.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    end = policy.settings.reference_date - datetime.timedelta(days=1)
    day = (end - policy.settings.month_start).days
    (row,) = [r for r in data["records"] if r["employee_id"] == employee_id]
    row["status"] = "terminated"
    row["end_date"] = end.isoformat()
    data["events"].append(
        {
            "day": day,
            "date": end.isoformat(),
            "employee_id": employee_id,
            "kind": "terminate",
            "fields": {"end_date": end.isoformat()},
            "source": "hr",
        }
    )
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return end.isoformat()


def coverage(
    world_dir: Path, employee_id: str, changes: list[dict[str, Any]], policy: Policy
) -> tuple[list[list[str]], list[list[str]]]:
    """(accounts of the leaver with a deprovisioning change, accounts without one)."""
    records, _ = load_hris(world_dir / "hris.json")
    state = actual_state(world_dir, policy)
    owned = sorted(
        key
        for key, m in match_accounts(state.accounts, records, policy).matches.items()
        if m.employee_id == employee_id
    )
    done = {
        (c["system"], c["app_id"], _norm(c["account"]))
        for c in changes
        if c["action"] in DEPROVISIONING
    }
    covered = [list(k) for k in owned if (k[0], k[1], _norm(k[2])) in done]
    missing = [list(k) for k in owned if (k[0], k[1], _norm(k[2])) not in done]
    return covered, missing


def run_drill(
    world_dir: Path, out: Path, policy: Policy, *, policy_dir: Path | None = None
) -> dict[str, Any]:
    world_dir, out = Path(world_dir), Path(out)
    copy = out / "world"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(world_dir, copy, ignore=shutil.ignore_patterns("answer_key.json"))
    employee_id = pick_leaver(copy, policy)
    before_main, before_held = split_plan(build_plan(copy, policy, policy_dir=policy_dir), policy)
    before = [*before_main.changes, *(before_held.changes if before_held else [])]
    covered_before, _ = coverage(copy, employee_id, before, policy)
    end = write_leaver(copy, employee_id, policy)
    # the HRIS change is written: from here to the plan files on disk is what is timed
    started = time.perf_counter()
    timings: dict[str, float] = {}
    combined = build_plan(copy, policy, timings=timings, policy_dir=policy_dir)
    main, held = split_plan(combined, policy)
    write_outputs(
        main, out / "plan", "https://example.com/plan-pull-request", False, timings, started, held
    )
    seconds = round(time.perf_counter() - started, 3)
    changes = [*main.changes, *(held.changes if held else [])]
    covered, missing = coverage(copy, employee_id, changes, policy)
    in_held = sum(1 for c in (held.changes if held else []) if c["employee_id"] == employee_id)
    result = {
        "employee_id": employee_id,
        "end_date": end,
        "seconds": seconds,
        "limit_seconds": LIMIT_SECONDS,
        "steps": timings,
        "accounts": len(covered) + len(missing),
        "covered": covered,
        "missing": missing,
        "changes_in_held_plan": in_held,
        "covered_before_the_change": len(covered_before),
        "ok": bool(covered) and not missing and not covered_before and seconds < LIMIT_SECONDS,
    }
    (out / RESULT_FILE).write_text(canonical_json(result) + "\n", encoding="utf-8")
    shutil.rmtree(copy)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.leaver_drill")
    parser.add_argument("--world", type=Path, required=True, help="a tuning world directory")
    parser.add_argument("--out", type=Path, required=True, help="where the drill writes")
    parser.add_argument("--policy-dir", type=Path, default=None, help="default: access/policy")
    args = parser.parse_args(argv)
    policy = load_policy(args.policy_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    result = run_drill(args.world, args.out, policy, policy_dir=args.policy_dir)
    print(
        f"leaver drill: {result['employee_id']}: {len(result['covered'])} of "
        f"{result['accounts']} accounts in the plan, {result['seconds']}s "
        f"(limit {LIMIT_SECONDS:.0f}s)"
    )
    for key in result["missing"]:
        print(f"  not in the plan: {' '.join(key)}")
    print("ok" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
