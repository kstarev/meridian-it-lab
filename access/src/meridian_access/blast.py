"""Blast radius on the tuning data: how many removals and suspensions, and from how many
people, each simulated day's HR events cause, and what the two-approver rule does with them.

Every removal or suspension in the plan (bulk-import ones included, they are what the day
would have caused; changes held for bad HR data are left out, as in the split) is attributed to
the day of the HR event that caused it, through the change's `cause_date` (the same attribution
the approval rule uses): a terminate event for a leaver, a move for a mover, a leave start for a
person on leave. A change with no HR event behind it (orphans, drift, people who left before the
month) has no cause date and goes to a separate backlog bucket, not to any day (the approval
rule counts that bucket's people together as one day). A contractor past their end date goes to
a day only if a terminate event is behind it, else to the backlog.

    python -m meridian_access.blast --world out/access/seed-6108 [--write-doc FILE]
"""

import argparse
import math
import re
from pathlib import Path
from typing import Any

from meridian_access.desired import load_hris
from meridian_access.diff import REMOVING
from meridian_access.limits import NO_DATE
from meridian_access.plan import build_plan, split_plan
from meridian_access.policy.load import Policy, load_policy

START = "<!-- access-blast-radius:start -->"
END = "<!-- access-blast-radius:end -->"
DAYS = 30


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
    day_of_date: dict[str, int] = {}
    bulk_days: set[int] = set()
    for ev in sorted(events, key=lambda e: (e.day, e.employee_id)):
        if not 0 <= ev.day < DAYS:
            raise ValueError(f"HR event for {ev.employee_id} on day {ev.day}: outside 0-{DAYS - 1}")
        day_of_date[ev.date] = ev.day
        if ev.kind == "terminate" and ev.source == "bulk_import":
            bulk_days.add(ev.day)
    per_day = [0] * DAYS
    people: list[set[str]] = [set() for _ in range(DAYS)]
    backlog = 0
    backlog_people: set[str] = set()
    for group in (plan.data["changes"], plan.data["held"]):
        for c in group:
            if c["action"] not in REMOVING or c.get("hold") == "bad_data":
                continue  # changes held for bad HR data are never applied: not in the split
            if not c["cause_date"] or not c["employee_id"]:
                backlog += 1
                if c["employee_id"]:
                    backlog_people.add(c["employee_id"])
                continue
            day = day_of_date.get(c["cause_date"])
            if day is None:
                raise ValueError(f"cause date {c['cause_date']} matches no HR event")
            per_day[day] += 1
            people[day].add(c["employee_id"])
    main, held_plan = split_plan(plan, policy)
    bulk_day = min(bulk_days) if bulk_days else None
    normal_days = [d for d in range(1, DAYS) if d not in bulk_days]
    normal = [per_day[d] for d in normal_days]
    max_people = policy.settings.blast_radius_people
    max_removals = policy.settings.blast_radius_removals
    ordinary = [d for d in range(DAYS) if d not in bulk_days and per_day[d]]
    biggest = max(ordinary, key=lambda d: (per_day[d], len(people[d]), -d)) if ordinary else None
    return {
        "per_day": per_day,
        "people_limit": max_people,
        "removals_limit": max_removals,
        "max": max(per_day),
        "p50": percentile(normal, 50),
        "p90": percentile(normal, 90),
        "bulk_day": bulk_day,
        "bulk_count": per_day[bulk_day] if bulk_day is not None else 0,
        "normal_max": max(per_day[d] for d in range(DAYS) if d not in bulk_days),
        "normal_max_people": len(people[biggest]) if biggest is not None else 0,
        "ordinary_days": len(ordinary),
        "ordinary_over_people": [d for d in ordinary if len(people[d]) >= max_people],
        "ordinary_over_removals": [d for d in ordinary if per_day[d] >= max_removals],
        "backlog": backlog,
        "people_per_day": [len(p) for p in people],
        "normal_people_max": max(len(people[d]) for d in range(DAYS) if d not in bulk_days),
        "bulk_people": len(people[bulk_day]) if bulk_day is not None else 0,
        "backlog_people": len(backlog_people),
        "main_changes": main.counts["total"],
        "main_approvals": main.data["approvals_required"],
        "held_buckets": main.data["held_plan"]["buckets"] if held_plan else [],
        "held_changes": held_plan.counts["total"] if held_plan else 0,
        "held_approvals": held_plan.data["approvals_required"] if held_plan else None,
        "as_of": plan.data["as_of"],
    }


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _held_sentence(report: dict[str, Any]) -> str:
    buckets = report["held_buckets"]
    if not buckets:
        return (
            f"On the whole-month tuning plan no bucket is over the limit: it is one plan and needs "
            f"{report['main_approvals']} approvals."
        )
    names = "; ".join(
        f"{'no date' if b['day'] == NO_DATE else b['day']} "
        f"({b['people']} people, {b['removals']} removals and suspensions)"
        for b in buckets
    )
    return (
        f"On the whole-month tuning plan these buckets are over the limit and go to the held "
        f"plan, a separate pull request: {names}. Every bulk import is held too. The held plan "
        f"has {report['held_changes']} changes and needs {report['held_approvals']} approvals; "
        f"the main plan has {report['main_changes']} changes and needs "
        f"{report['main_approvals']}."
    )


def render(report: dict[str, Any]) -> str:
    people_limit = report["people_limit"]
    removals_limit = report["removals_limit"]
    per_day = report["per_day"]
    people = report["people_per_day"]
    bulk = report["bulk_day"]
    bulk_people_hit = report["bulk_people"] >= people_limit
    bulk_removals_hit = report["bulk_count"] >= removals_limit
    lines = [
        "| Statistic | Removals and suspensions |",
        "|---|---|",
        f"| Maximum event day | {report['max']} |",
        f"| Median (p50), event days 1-29 excluding the bulk-import day | {report['p50']} |",
        f"| p90, event days 1-29 excluding the bulk-import day | {report['p90']} |",
        f"| Largest event day that is not the bulk import | {report['normal_max']} |",
        f"| Bulk-import day (day {bulk}) | {report['bulk_count']} |",
        f"| Backlog (orphans, drift, earlier leavers: no event day) | {report['backlog']} |",
        f"| People with a backlog removal and an owner (the rule counts them as one day) | "
        f"{report['backlog_people']} |",
        f"| `blast_radius_people` in `settings.yaml` | {people_limit} |",
        f"| `blast_radius_removals` in `settings.yaml` | {removals_limit} |",
        "",
        "| Day | Removals and suspensions | People |",
        "|---|---|---|",
    ]
    lines += [f"| {d} | {c} | {people[d]} |" for d, c in enumerate(per_day) if c]
    lines += [
        "",
        f"The limit is counted per cause-day: a day's bucket is over it when that day's HR event "
        f"removes or suspends access for {people_limit} or more people, or {removals_limit} or "
        f"more accesses. Removals with no event day form one extra bucket, checked the same way. "
        f"On the tuning data, {report['ordinary_days']} ordinary event days have removals:",
        "",
        "| Leg | Ordinary event days needing two approvals | Bulk-import day needs two |",
        "|---|---|---|",
        f"| People on one day, {people_limit} or more | {len(report['ordinary_over_people'])} | "
        f"{_yes(bulk_people_hit)} ({report['bulk_people']} people) |",
        f"| Removals on one day, {removals_limit} or more | "
        f"{len(report['ordinary_over_removals'])} | "
        f"{_yes(bulk_removals_hit)} ({report['bulk_count']} removals) |",
        "",
        f"The largest ordinary day has {report['normal_max']} removals and suspensions from "
        f"{report['normal_max_people']} people; the most people on any ordinary day is "
        f"{report['normal_people_max']}. Counting people separates the bulk-import day from "
        f"ordinary days; the removals number catches a few people with very many accesses.",
        "",
        f"{_held_sentence(report)}",
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
