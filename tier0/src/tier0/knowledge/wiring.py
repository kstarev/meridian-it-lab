"""The guide picker in the Slack flow (milestone 4: ADR-035, ADR-040, ADR-046).

It is off unless the agent is given a `GuidePicker` (the agent option `guide_picker`, default
None). Off, every message triage calls "other" goes to a person, exactly as before.

On, a message goes the knowledge way only when all of these hold:

- identity binding passed, and the kill switch and the rate limit let the request through,
  exactly as for every request;
- triage really called it "other": a failed call, a refusal or an off-format reply also reads
  as "other", but it goes to a person and the picker never sees it (as in the month's routing);
- the policy table still says "escalate" for "other".

With a how-to gate set (milestone 3f, `HOW_TO_GATE_VERSION`; G3 by default, the version the
3f dev tuning picked), two steps come first, and each can only hand off: the code-only message
screen (if it finds anything, the picker is never asked), then the gate on triage's own model
(tier0.knowledge.gate), which must say exactly yes. A failed call, a refusal or a reply off the
schema hands off. With `gate_version=None` on the picker, the path runs exactly as before.

Then `answer_question` decides, with every gate it has: the frozen picker (KS5 on Claude
Sonnet 5.5, ADR-046), the code-only message screen, and the posting gate (a guide approved by
a person and not flagged by the guide screen). The model sees ids, titles and summaries, never
steps. Code posts the approved steps word for word, or the message goes to a person. Since
ADR-053, by default, the guide goes first to #it-team as a draft, and code posts it only after
one click from a person in IT (tier0.knowledge.review); the requester gets the hand-off reply.

The picker's model is injected, so tests use a fake and the lab makes no paid call. A real
Claude model must be Sonnet 5.5: the prompt is frozen for it, and Haiku failed the sealed
test's rule 2 (ADR-046).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from meridian_dataset.knowledge import MEMORY_REPO_URL, Guide, load_runbooks
from tier0.knowledge.answer import Outcome, answer_question, postable
from tier0.knowledge.gate import GATE_VERSIONS, gate_request, parse_gate
from tier0.knowledge.prompts import FROZEN, PICKER_VERSIONS
from tier0.models.base import ModelAdapter, ModelError

# ADR-046: the guide picker runs on Claude Sonnet 5.5, with its frozen prompt.
PICKER_MODEL = "claude-sonnet-5-5"
PICKER_VERSION = FROZEN[PICKER_MODEL]
# Milestone 3f: the how-to gate's version (tier0.knowledge.gate), asked on triage's own model
# before the picker. G3 is the version the 3f dev tuning picked on both arms, by the plan's
# selection rule (docs/plans/milestone-3f-how-to-gate.md); the blind month3 will measure it.
# A picker built with gate_version=None has no gate, and its path runs exactly as before.
HOW_TO_GATE_VERSION: str | None = "G3"
# Why a message was handed off before the picker was asked, with a gate set. The agent's
# hand-off codes are these after "guide_" (guide_screen_first, ...); "guide_screen" stays the
# code for the screen firing after a picker call, with no gate.
SCREEN_FIRST = "screen_first"  # code: the message screen found something; no gate, no picker
GATE_NO = "gate_no"  # the gate said the message is not one plain how-to question
GATE_FAILED = "gate_failed"  # the gate's call failed, it refused, or its reply was off the schema
# A provider that names real Claude models; any of its models but PICKER_MODEL is refused.
CLAUDE_PROVIDER = "anthropic"
# Why a guide the gates allowed was still not posted (it can't happen; code checks anyway).
NOT_POSTABLE = "not_postable"

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class GuidePicker:
    """Turns the knowledge path on. `model` answers the picker's call; `guides` are the
    approved runbooks (by default the lab's 30). The prompt is always the frozen one.
    `gate_version` is the how-to gate asked before the picker (milestone 3f), or None for none;
    the gate runs on the model the agent passes, triage's own, never on `model`."""

    model: ModelAdapter
    guides: tuple[Guide, ...] = field(default_factory=load_runbooks)
    gate_version: str | None = HOW_TO_GATE_VERSION

    def __post_init__(self) -> None:
        if self.model.provider == CLAUDE_PROVIDER and self.model.model != PICKER_MODEL:
            raise ValueError(
                f"the guide picker runs on {PICKER_MODEL} with {PICKER_VERSION} (ADR-046), "
                f"not {self.model.model}"
            )
        if self.gate_version is not None and self.gate_version not in GATE_VERSIONS:
            raise ValueError(f"there is no how-to gate {self.gate_version!r}")

    @property
    def version(self) -> str:
        return PICKER_VERSION

    @property
    def system(self) -> str:
        return PICKER_VERSIONS[PICKER_VERSION]

    def answer(self, text: str, *, model: ModelAdapter | None = None) -> Outcome:
        """One message: a guide to post, or a hand-off. `model` stands in for `self.model`
        on this call only; the agent passes the same model wrapped in its trace session."""
        return answer_question(
            text,
            model=self.model if model is None else model,
            guides=self.guides,
            system=self.system,
        )

    def ask_gate(self, text: str, *, model: ModelAdapter) -> bool | None:
        """The how-to gate's verdict on one message, from `model` (triage's own): True only for
        exactly {"plain_how_to": true}, False for exactly false, None for a failed call, a
        refusal, a reply cut short or anything off the schema. Only True lets the picker be
        asked. The gate never sees a guide."""
        if self.gate_version is None:
            raise ValueError("no how-to gate is set")
        try:
            response = model.complete(gate_request(text, GATE_VERSIONS[self.gate_version]))
        except ModelError:
            return None
        return parse_gate(response)


def postable_guide(guides: Sequence[Guide], guide_id: str | None) -> Guide | None:
    """The guide to post, looked up again among the postable guides (approved by a person,
    not flagged by the guide screen), or None."""
    if guide_id is None:
        return None
    return next((g for g in postable(guides) if g.id == guide_id), None)


def code_link(guide: Guide) -> str | None:
    """The guide's link, when code built it from the memory repo's address and a plain slug.
    The output guard exempts only this exact string; any other link is checked as usual."""
    if not _SLUG.fullmatch(guide.slug):
        return None
    link = guide.link
    return link if link == f"{MEMORY_REPO_URL}/{guide.slug}.md" else None
