"""Decide and reply, in code (ADR-035, ADR-028).

The model's pick is only a suggestion. Code posts a guide when all of these hold: the model
called the message a how-to, it picked a guide, that guide is an approved runbook, and the
code-only screen didn't fire. Otherwise the message goes to a person. The reply is the guide's
approved steps, word for word, with a link; the model never writes any of it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from meridian_dataset.knowledge import Guide
from tier0.knowledge.picker import HOW_TO, Entry, Pick, parse_pick, pick_request
from tier0.knowledge.screen import hand_off_signal
from tier0.models.base import ModelAdapter, ModelError

GUIDE = "guide"
HAND_OFF = "hand_off"
# Why a message was handed off.
MODEL_ERROR = "model_error"
OFF_FORMAT = "off_format"
NOT_A_HOW_TO = "not_a_how_to"
NO_GUIDE_FITS = "no_guide_fits"
NOT_APPROVED = "not_an_approved_guide"
SCREEN = "screen"

_HEADERS = {
    "en": "Here's our guide for that:",
    "de": "Dafür haben wir eine Anleitung:",
    "es": "Tenemos una guía para eso:",
    "fr": "Nous avons un guide pour ça :",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: str  # GUIDE or HAND_OFF
    guide: str | None
    why: str | None  # set for a hand-off
    pick: Pick | None  # what the model said, if it answered
    screen: str | None  # what the code-only screen said
    input_tokens: int = 0
    output_tokens: int = 0


def entries_for(guides: Sequence[Guide]) -> tuple[Entry, ...]:
    """The list the model sees: each approved guide (the steps are shown only in the sealed
    test's poisoned-guide questions)."""
    return tuple(
        Entry(id=g.id, title=g.title, summary=g.summary, steps=g.steps)
        for g in guides
        if g.approved
    )


def decide(
    pick: Pick, approved: frozenset[str], screen: str | None, *, use_screen: bool = True
) -> tuple[str, str | None, str | None]:
    """(kind, guide, why) for one parsed pick. Fails closed at every step."""
    if pick.off_format:
        return HAND_OFF, None, OFF_FORMAT
    if use_screen and screen is not None:
        return HAND_OFF, None, SCREEN
    if pick.request_type != HOW_TO:
        return HAND_OFF, None, NOT_A_HOW_TO
    if pick.guide is None:
        return HAND_OFF, None, NO_GUIDE_FITS
    if pick.guide not in approved:
        return HAND_OFF, None, NOT_APPROVED
    return GUIDE, pick.guide, None


def answer_question(
    text: str,
    *,
    model: ModelAdapter,
    guides: Sequence[Guide],
    system: str,
    use_screen: bool = True,
    entries: Sequence[Entry] | None = None,
    show_steps: bool = False,
) -> Outcome:
    """One how-to message from a verified employee: a guide to post, or a hand-off.

    `entries` replaces the guide list the model sees (the eval adds a poisoned guide to it);
    only approved guides can ever be posted, whatever the list holds."""
    approved = entries_for(guides)
    entries = approved if entries is None else tuple(entries)
    screen = hand_off_signal(text)
    try:
        response = model.complete(pick_request(text, entries, system, show_steps=show_steps))
    except ModelError:
        return Outcome(HAND_OFF, None, MODEL_ERROR, None, screen)
    pick = parse_pick(response, entries)
    kind, guide, why = decide(
        pick, frozenset(e.id for e in approved), screen, use_screen=use_screen
    )
    return Outcome(kind, guide, why, pick, screen, response.input_tokens, response.output_tokens)


def reply_text(guide: Guide, lang: str = "en") -> str:
    """The reply code posts: the approved steps word for word, and the link."""
    lines = [_HEADERS.get(lang, _HEADERS["en"]), f"*{guide.title}*"]
    lines += [f"{i}. {step}" for i, step in enumerate(guide.steps, 1)]
    lines.append(guide.link)
    return "\n".join(lines)
