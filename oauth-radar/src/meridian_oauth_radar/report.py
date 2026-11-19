"""The weekly report for IT and Security, in Markdown, built only from a run's files.

`weekly_report(files)` takes the run's files by path (as `run.run_files` returns them, or as
`read_run` reads them back from a folder) and nothing else, so the report can be rebuilt
from the evidence a run left. Findings are grouped by app, not by person: the only people
named are the ones a plan or a nudge draft is about, because IT needs them to act.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FILES = (
    "run.json",
    "scores.json",
    "plans/after-notice-plan.json",
    "plans/emergency-plan.json",
    "notices/ledger.json",
    "to-a-person.json",
    "nudges/index.json",
)


def read_run(folder: Path) -> dict[str, bytes]:
    """The files the report needs, read back from a run's folder."""
    return {name: (folder / name).read_bytes() for name in FILES}


def _load(files: dict[str, bytes], name: str) -> Any:
    return json.loads(files[name])


def _plan(lines: list[str], title: str, plan: dict[str, Any]) -> None:
    lines += [f"## {title}", "", f"Status: **{plan['status']}**. {plan['note']}", ""]
    if plan["items"]:
        lines += ["| App | User | Why | Notice |", "|---|---|---|---|"]
        lines += [
            f"| {i['app']} | {i['user_key']} | {i['reason']} | {i['notice_issued'] or 'none'} |"
            for i in plan["items"]
        ]
        lines.append("")


def weekly_report(files: dict[str, bytes]) -> str:
    run = _load(files, "run.json")
    found = _load(files, "scores.json")
    after = _load(files, "plans/after-notice-plan.json")
    urgent = _load(files, "plans/emergency-plan.json")
    ledger = _load(files, "notices/ledger.json")
    person = _load(files, "to-a-person.json")
    drafts = _load(files, "nudges/index.json")
    c = run["counts"]
    tiers = c["tiers"]
    day = run["for_date"]

    lines = [
        f"# Shadow AI Radar: weekly report, {day}",
        "",
        "For IT and Security. **A dry run:** nothing was removed and no message was sent. "
        "Every plan below waits for a person. "
        f"Risk scores use the OAR-002 rubric: {run['rubric']}.",
        "",
        "## This week",
        "",
        "| | Count |",
        "|---|---|",
        f"| People scanned | {c['users']} |",
        f"| Grants | {c['grants']} |",
        f"| Apps | {c['apps']} |",
        f"| Rated high / medium / low | {tiers['high']} / {tiers['medium']} / {tiers['low']} |",
        f"| Unused for over {run['settings']['dormant_days']} days, with a restricted scope "
        f"| {c['dormant_restricted']} |",
        f"| Notice drafts written this run | {c['notices_new']} |",
        f"| Notices still in their {run['settings']['notice_days']} days "
        f"| {c['notices_waiting']} |",
        f"| In the after-notice plan | {c['after_notice_plan']} ({after['status']}) |",
        f"| In the emergency plan | {c['emergency_plan']} ({urgent['status']}) |",
        f"| For a person to review | {c['to_a_person']} |",
        f"| Under a current exception | {c['excepted']} |",
        f"| Exceptions past their expiry | {c['exceptions_expired']} |",
        f"| Slack nudge drafts | {c['nudges']} |",
        f"| Changes made / messages sent | {run['changes_made']} / {run['messages_sent']} |",
        "",
    ]
    _plan(lines, "Emergency: compromised apps (a person must trigger this plan)", urgent)
    _plan(lines, "After-notice removal plan", after)

    lines += ["## For a person to review, by app", ""]
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in person["grants"]:
        by_app[row["app"]].append(row)
    if by_app:
        lines += ["| App | Grants | Tier | Points | Why |", "|---|---|---|---|---|"]
        ordered = sorted(
            by_app.items(),
            key=lambda kv: (kv[1][0]["tier"] != "high", -kv[1][0]["total"], kv[0].casefold()),
        )
        for app, rows in ordered:
            reasons = Counter(r["reason"] for r in rows).most_common(1)[0][0]
            tiers_here = "/".join(sorted({r["tier"] for r in rows}))
            totals = sorted({r["total"] for r in rows})
            points = str(totals[0]) if len(totals) == 1 else f"{totals[0]}-{totals[-1]}"
            lines.append(f"| {app} | {len(rows)} | {tiers_here} | {points} | {reasons} |")
    else:
        lines.append("Nothing.")
    lines.append("")

    lines += ["## Notices", ""]
    waiting = Counter(n["app"] for n in ledger["notices"])
    if waiting:
        lines += ["| App | Open notices |", "|---|---|"]
        lines += [f"| {app} | {n} |" for app, n in sorted(waiting.items())]
    else:
        lines.append("No open notices.")
    lines.append("")

    lines += ["## Slack nudge drafts, by app", ""]
    nudged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in drafts["drafts"]:
        nudged[row["app"]].append(row)
    if nudged:
        lines += ["| App | Drafts | Approved alternative |", "|---|---|---|"]
        lines += [
            f"| {app} | {len(rows)} | {rows[0]['alternative'] or 'none yet'} |"
            for app, rows in sorted(nudged.items())
        ]
    else:
        lines.append("None.")
    lines.append("")

    lines += [
        "## Every app, by risk",
        "",
        "Points per signal, for the app's first grant: scope breadth + unverified publisher + "
        "reach + AI app + not reviewed.",
        "",
        "| App | Grants | Tier | Points | Vendor | Data location | IT |",
        "|---|---|---|---|---|---|---|",
    ]
    apps: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in found["grants"]:
        apps[row["client_id"]].append(row)
    rank = {"high": 0, "medium": 1, "low": 2}

    def order(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int, str]:
        first = item[1][0]
        return (rank[first["tier"]], -first["total"], first["app"].casefold())

    for client, rows in sorted(apps.items(), key=order):
        first = rows[0]
        parts = "+".join(str(p["points"]) for p in first["signals"])
        info = run["apps"][client]
        override = " (override)" if first["overrides"] else ""
        lines.append(
            f"| {first['app']} | {len(rows)} | {first['tier']}{override} "
            f"| {parts} = {first['total']} "
            f"| {info['vendor'] or 'unknown'} | {info['data_location'] or 'unknown'} "
            f"| {info['it_status'] or 'not reviewed'} |"
        )
    lines += [
        "",
        f"Made by the radar from the run's files ({run['api_calls']} calls to the mock Admin "
        "API, all reads). Every name, app and number above is fake.",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["FILES", "read_run", "weekly_report"]
