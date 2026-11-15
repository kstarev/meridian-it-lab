"""Blast radius on the tuning data: how many removals and suspensions each simulated day's
HR events cause, and whether the limit N separates a normal day from the bulk-import day.

Every removal or suspension in the plan (held ones included, they are what the day would
have caused) is attributed to the day of the HR event that caused it: a terminate event for
a leaver, a move for a mover, a leave start for a person on leave. A change with no HR event
behind it (orphans, drift, contractors past their end date, people who left before the
month) goes to a separate backlog bucket, not to any day.

    python -m meridian_access.blast --world out/access/seed-6108 [--write-doc FILE]
"""

import argparse
import math
import re
from pathlib import Path
from typing import Any

from meridian_access.desired import load_hris
from meridian_access.diff import MIN_BATCH_PEOPLE, REMOVING
from meridian_access.plan import build_plan
from meridian_access.policy.load import Policy, load_policy

START = "<!-- access-blast-radius:start -->"
END = "<!-- access-blast-radius:end -->"
DAYS = 30
CAUSE_KIND = {
    "leaver_access_left": "terminate",
    "contractor_past_end": "terminate",
    "orphaned_account": "terminate",
    "mover_stale_access": "move",
    "leave_of_absence": "leave_start",
}


def percentile(values: list[int], q: float) -> int:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def blast_radius_report(world_dir: Path | str, policy: Policy) -> dict[str, Any]:
    """Removals and suspensions per simulated day (0-29) for a world directory, with the
    changes that have no HR event behind them in a separate backlog bucket."""
    root = Path(world_dir)
    plan = build_plan(root, policy)
    _, events = load_hris(root / "hris.json")
    day_of: dict[tuple[str, str], int] = {}
    bulk_days: set[int] = set()
    for ev in sorted(events, key=lambda e: (e.day, e.employee_id)):
        if not 0 <= ev.day < DAYS:
            raise ValueError(f"HR event for {ev.employee_id} on day {ev.day}: outside 0-{DAYS - 1}")
        day_of[(ev.employee_id, ev.kind)] = ev.day  # the last one wins
        if ev.kind == "terminate" and ev.source == "bulk_import":
            bulk_days.add(ev.day)
    per_day = [0] * DAYS
    people: list[set[str]] = [set() for _ in range(DAYS)]
    leaver_removals: dict[str, int] = {}  # per ordinary leaver (not in a bulk-import day)
    backlog = 0
    for c in [*plan.data["changes"], *plan.data["held"]]:
        if c["action"] not in REMOVING:
            continue
        kind = CAUSE_KIND.get(c["type"], "")
        day = day_of.get((c["employee_id"], kind)) if kind and c["employee_id"] else None
        if day is None:
            backlog += 1
            continue
        if not 0 <= day < DAYS:
            raise ValueError(f"day {day} is outside 0-{DAYS - 1}")
        per_day[day] += 1
        people[day].add(c["employee_id"])
        if kind == "terminate" and day not in bulk_days:
            leaver_removals[c["employee_id"]] = leaver_removals.get(c["employee_id"], 0) + 1
    bulk_day = min(bulk_days) if bulk_days else None
    normal_days = [d for d in range(1, DAYS) if d not in bulk_days]
    normal = [per_day[d] for d in normal_days]
    n_limit = policy.settings.blast_radius_n
    return {
        "per_day": per_day,
        "limit": n_limit,
        "max": max(per_day),
        "p50": percentile(normal, 50),
        "p90": percentile(normal, 90),
        "bulk_day": bulk_day,
        "bulk_count": per_day[bulk_day] if bulk_day is not None else 0,
        "normal_max": max(per_day[d] for d in range(DAYS) if d not in bulk_days),
        "normal_over_limit": [
            d for d in range(DAYS) if d not in bulk_days and per_day[d] > n_limit
        ],
        "backlog": backlog,
        "people_per_day": [len(p) for p in people],
        "normal_people_max": max(len(people[d]) for d in range(DAYS) if d not in bulk_days),
        "per_leaver": sum(leaver_removals.values()) / len(leaver_removals)
        if leaver_removals
        else 0.0,
        "bulk_people": len(people[bulk_day]) if bulk_day is not None else 0,
        "plan_removals": plan.counts["removals_and_suspensions"],
        "plan_approvals": plan.data["approvals_required"],
        "as_of": plan.data["as_of"],
    }


def _round_up(value: int) -> int:
    return (value // 5 + 1) * 5


def render(report: dict[str, Any]) -> str:
    n = report["limit"]
    per_day = report["per_day"]
    lines = [
        "| Statistic | Removals and suspensions |",
        "|---|---|",
        f"| Maximum event day | {report['max']} |",
        f"| Median (p50), event days 1-29 excluding the bulk-import day | {report['p50']} |",
        f"| p90, event days 1-29 excluding the bulk-import day | {report['p90']} |",
        f"| Largest event day that is not the bulk import | {report['normal_max']} |",
        f"| Bulk-import day (day {report['bulk_day']}) | {report['bulk_count']} |",
        f"| Backlog (orphans, drift, contractors past end, earlier leavers: no event day) | "
        f"{report['backlog']} |",
        f"| Limit N in `settings.yaml` | {n} |",
        "",
        "| Day | Removals and suspensions | People |",
        "|---|---|---|",
    ]
    people = report["people_per_day"]
    lines += [f"| {d} | {c} | {people[d]} |" for d, c in enumerate(per_day) if c]
    lines.append("")
    over = report["normal_over_limit"]
    k = MIN_BATCH_PEOPLE
    if over:
        days = ", ".join(str(d) for d in over)
        worst = max(per_day[d] for d in over)
        typical = report["per_leaver"]
        bigger = _round_up(worst)
        needed = int(bigger // typical) + 1 if typical else 0
        lines.append(
            f"N = {n} does not cleanly separate normal days from the bulk-import day: day(s) "
            f"{days} are ordinary event days above {n} (the largest has {worst}, from only "
            f"{max(people[d] for d in over)} people), while the bulk-import day has "
            f"{report['bulk_count']} from {report['bulk_people']} people."
        )
        lines += [
            "",
            f"Proposal, for a policy pull request (settings.yaml is not changed here): count "
            f"people as well as accesses, and require two approvals when one day's HR events "
            f"touch {k} or more people (an ordinary day touched at most "
            f"{report['normal_people_max']}, the bulk-import day {report['bulk_people']}). The "
            f"threshold is `MIN_BATCH_PEOPLE` in `diff.py`, the same constant the diff already "
            f"uses before it holds a same-day batch of terminations, so there is one number for "
            f'"how many people at once is a batch" and not two.',
            "",
            f"Raising N to {bigger} is not recommended: {needed} ordinary leavers (about "
            f"{round(needed * typical)} removals at the ordinary average of {typical:.1f} per "
            f"leaver) already exceed it, so it would still hold up ordinary days, and a bulk "
            f"change of a few people with few accesses each would get through.",
        ]
    else:
        lines.append(
            f"N = {n} separates normal days (at most {report['normal_max']}) from the "
            f"bulk-import day ({report['bulk_count']})."
        )
    lines += [
        "",
        f"This analysis assumes one plan per day. The current tuning plan covers the whole "
        f"month at once: {report['plan_removals']} removals and suspensions that are not held, "
        f"so it needs {report['plan_approvals']} approvals regardless of any per-day rule.",
    ]
    return "\n".join(lines) + "\n"


def replace_block(doc: str, body: str) -> str:
    if START not in doc or END not in doc:
        raise ValueError(f"the document needs {START} and {END}")
    head, rest = doc.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{body}{END}{tail}"


def read_block(doc: str) -> str:
    m = re.search(re.escape(START) + r"\n(.*?)" + re.escape(END), doc, re.S)
    if not m:
        raise ValueError("no access-blast-radius block")
    return m.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meridian_access.blast")
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--write-doc", type=Path, help="refresh the marked block in this file")
    args = parser.parse_args(argv)
    body = render(blast_radius_report(args.world, load_policy()))
    print(body, end="")
    if args.write_doc:
        args.write_doc.write_text(
            replace_block(args.write_doc.read_text(encoding="utf-8"), body), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
