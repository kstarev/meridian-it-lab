"""Slack nudge drafts, as files: for risky apps people still use. Nothing is ever sent.

The catalogue: "for risky-but-used apps, messages the user in Slack: 'This app can read all
your Drive files. Here's the approved tool that does the same thing.'" Here each message is a
**draft file**, one per user. The radar holds no Slack client and no token, and sends nothing.

A grant gets a nudge when the policy sent it to a person, the rubric rates it high, it was
used within the dormancy window, and IT hasn't approved the app. (Unused grants get a
notice instead, compromised apps an emergency plan, and approved apps no nudge.) The message
says what the app can do, in the scope explainer's words for its broadest scope, and names
the approved alternative in the same category, if IT has one.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meridian_oauth_radar import scopes
from meridian_oauth_radar.enrich import APPROVED, Knowledge
from meridian_oauth_radar.policy import PolicyResult, days_unused
from meridian_oauth_radar.rubric import HIGH, breadth


@dataclass(frozen=True, slots=True)
class Nudge:
    user_key: str
    client_id: str
    app: str
    can: str
    alternative: str | None

    @property
    def text(self) -> str:
        first = f"*{self.app}* is connected to your Google account. {self.can}"
        if self.alternative:
            return (
                f"{first} Here's the approved tool that does the same thing: "
                f"*{self.alternative}*. If you switch, you can disconnect {self.app}."
            )
        return (
            f"{first} IT hasn't approved a tool for this yet, so a person in IT will look at "
            "it and may get in touch."
        )


def nudges(result: PolicyResult, knowledge: Knowledge) -> tuple[Nudge, ...]:
    """Every nudge to draft, sorted by user and app."""
    found = []
    for routed in result.to_a_person:
        score = routed.score
        grant = score.grant
        unused = days_unused(grant, result.today)
        if score.tier != HIGH or unused is None or unused > result.settings.dormant_days:
            continue
        facts = knowledge.facts(grant.client_id)
        if facts.it_status == APPROVED:
            continue
        _, widest = breadth(grant.scopes)
        can = scopes.explain(widest) if widest else "This app holds no scopes."
        found.append(
            Nudge(
                grant.user_key, grant.client_id, grant.display_text, can, facts.approved_alternative
            )
        )
    return tuple(sorted(found, key=lambda n: (n.user_key, n.app.casefold(), n.client_id)))


def nudge_files(found: tuple[Nudge, ...], today_iso: str) -> dict[str, bytes]:
    """One Markdown draft per user, and an index, by path."""
    by_user: dict[str, list[Nudge]] = {}
    for nudge in found:
        by_user.setdefault(nudge.user_key, []).append(nudge)
    files: dict[str, bytes] = {}
    for user, theirs in sorted(by_user.items()):
        lines = [
            f"# Slack message draft for {user}",
            "",
            f"Draft of {today_iso}. Not sent: the radar sends nothing.",
            "",
            *(f"> {n.text}\n" for n in theirs),
        ]
        files[f"nudges/{user}.md"] = ("\n".join(lines).rstrip() + "\n").encode()
    index: Mapping[str, Any] = {
        "for_date": today_iso,
        "sent": 0,
        "drafts": [
            {
                "user_key": n.user_key,
                "client_id": n.client_id,
                "app": n.app,
                "alternative": n.alternative,
            }
            for n in found
        ],
    }
    files["nudges/index.json"] = (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
    return files


__all__ = ["Nudge", "nudge_files", "nudges"]
