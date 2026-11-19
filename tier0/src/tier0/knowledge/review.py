"""One click from a person in IT before a guide answer is posted (ADR-053).

The owner's ruling 12b (2026-11-19): guide answers wait for one click from a person in IT
until a blind month passes. The picker failed rule 1 on both blind months of milestone 3e
(docs/evals/2026-11-19-m3e-failure-note.md): it answered some requests meant for a person
with an approved guide. So the picker's choice is now a suggestion.

With `GUIDE_ANSWERS_NEED_IT_CLICK` on (tier0.controls, the default), the agent does this when
the picker chooses a guide that every gate allows:

- the requester gets the existing hand-off reply (IT will look at it), and the ticket goes to
  the human queue, exactly as a hand-off does;
- a draft goes to #it-team: what tier0 would post, word for word, with two buttons, "Send this
  guide" and "Take it over";
- only a signed click from #it-team by a member of the IT team sends the guide, once. Any other
  click is refused and written to the audit log. "Take it over" leaves the ticket with the
  person, and tier0 sends nothing.

This module holds the fixed parts: the button ids, the draft's text and blocks (code writes
them; no model), and who counts as the IT team. The flow itself is in tier0.agent.

The evals don't change: the month runner and the how-to eval score the picker's choice, and
this gate sits after it.
"""

from typing import Any

from meridian_dataset import World
from meridian_dataset.knowledge import Guide
from tier0.mocks.slack import CHANNELS

SEND_GUIDE_ACTION = "send_guide"
TAKE_OVER_ACTION = "take_over_guide"
ACTIONS = (SEND_GUIDE_ACTION, TAKE_OVER_ACTION)
# The channel the draft goes to and the only one a click counts from: #it-team, where the IT
# team already gets the proactive posts' previews (ADR-020).
IT_TEAM_CHANNEL = CHANNELS["it-team"]
# The IT team, as the directory has it: the IT department. The weekly drafting job asks the
# same people to review guide drafts (tier0.knowledge.drafting.it_team).
IT_DEPARTMENT = "IT"
# Slack's limit for one section block's text.
_SECTION_LIMIT = 3000

DRAFT_HEADER = (
    "Guide draft for {ticket}. The guide picker chose {guide}, *{title}*. The requester has only "
    "been told that IT will look at it. *Send this guide* posts the reply below to them, word "
    "for word. *Take it over* leaves the ticket with you, and tier0 sends nothing."
)


def it_team(world: World) -> frozenset[str]:
    """The Slack user ids whose click may send a guide: the world's IT department, active in
    the directory and in Slack."""
    return frozenset(
        p.slack_user_id
        for p in world.people
        if p.department == IT_DEPARTMENT and p.status == "active" and p.slack_active
    )


def draft_header(ticket: str, guide: Guide) -> str:
    return DRAFT_HEADER.format(ticket=ticket, guide=guide.id, title=guide.title)


def draft_text(ticket: str, guide: Guide, reply: str) -> str:
    """The draft's plain text: the header, then the reply as the requester would get it."""
    return f"{draft_header(ticket, guide)}\n\n{reply}"


def draft_blocks(ticket: str, guide: Guide, reply: str) -> list[dict[str, Any]]:
    """The header, the reply (Slack shows blocks, not `text`, when both are sent), then the two
    buttons. Each button's value is the ticket, so a click names the draft it is for."""
    parts = [reply[i : i + _SECTION_LIMIT] for i in range(0, len(reply), _SECTION_LIMIT)]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": draft_header(ticket, guide)}},
        *({"type": "section", "text": {"type": "plain_text", "text": part}} for part in parts),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": SEND_GUIDE_ACTION,
                    "value": ticket,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Send this guide"},
                },
                {
                    "type": "button",
                    "action_id": TAKE_OVER_ACTION,
                    "value": ticket,
                    "text": {"type": "plain_text", "text": "Take it over"},
                },
            ],
        },
    ]
