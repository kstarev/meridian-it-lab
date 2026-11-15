"""The mock Slack message (Block Kit JSON). It is only written to a file, never sent.

There is no network code here and no token or webhook anywhere (AAC-008): a real Slack
adapter needs the owner's workspace and is not part of this lab. Everything in the message
comes from plan.json and is also shown in PLAN.md.
"""

import json
from pathlib import Path
from typing import Any

from meridian_access.plan import SECTIONS, Plan

MAX_HEADER = 150
MAX_SECTION = 3000


def escape_mrkdwn(text: str) -> str:
    """Escape the characters Slack's mrkdwn treats as markup in a link target. A `|` would end
    the link target, so it is percent-encoded."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "%7C")


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:MAX_SECTION]}}


def build_message(plan: Plan, pr_url: str) -> dict[str, Any]:
    data = plan.data
    counts = plan.counts
    header = f"Access plan for {data['as_of']}"
    by_type = counts["by_type"]
    lines = [f"*{counts['total']} changes*"]
    lines += [f"• {title}: {by_type[key]}" for key, title in SECTIONS if by_type.get(key)]
    warnings = [f"• Approvals required: {data['approvals_required']}"]
    if by_type.get("hris_mass_termination"):
        warnings.append("• Bulk HR change held, two approvers needed")
    if counts["held"]:
        warnings.append(f"• Changes held, not applied: {counts['held']}")
    if by_type.get("drift"):
        warnings.append(f"• Drift (changed outside the process): {by_type['drift']}")
    if by_type.get("break_glass"):
        warnings.append(f"• Break-glass entries seen, never touched: {by_type['break_glass']}")
    if counts["warnings"]:
        warnings.append(f"• Warnings in the plan: {counts['warnings']}")
    if counts["manual_tasks"]:
        warnings.append(f"• Manual tasks: {counts['manual_tasks']}")
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:MAX_HEADER]}},
        _section("\n".join(lines)),
        _section("*Warnings*\n" + "\n".join(warnings)),
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{escape_mrkdwn(pr_url)}|Review the plan pull request>",
                }
            ],
        },
    ]
    return {
        "text": f"{header}: {counts['total']} changes, "
        f"{data['approvals_required']} approval(s) required",
        "blocks": blocks,
    }


def write_message(message: dict[str, Any], path: Path) -> None:
    Path(path).write_text(
        json.dumps(message, sort_keys=True, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
