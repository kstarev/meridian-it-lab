"""Decide and reply, in code (ADR-035, ADR-028).

The model's pick is only a suggestion. Code posts a guide when all of these hold: the model
called the message a how-to, it picked a guide, that guide is an approved runbook that the
guide screen doesn't flag, and the message screen didn't fire. Otherwise the message goes to a
person. The 3e candidates add conditions: with asks_for, "person" is always handed off and
"nothing" gets a guide only if it is the pointer guide; with the fit check, a second call must
say the chosen guide fits. The reply is the guide's approved steps, word for word, with a link;
the model never writes any of it.

The model is not the defence against a poisoned guide: a person's approval and the guide screen
are. A flagged guide is never shown to the model and never posted.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from meridian_dataset.knowledge import Guide
from tier0.knowledge.guide_screen import flag_guide
from tier0.knowledge.picker import (
    HOW_TO,
    Entry,
    Pick,
    fit_request,
    parse_fit,
    parse_pick,
    pick_request,
)
from tier0.knowledge.prompts import ASKS_NOTHING, ASKS_PERSON
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
NO_REQUEST = "no_request"
NEEDS_A_PERSON = "needs_a_person"
FIT_NO = "fit_check_no"
FIT_FAILED = "fit_check_failed"

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
    fit: bool | None = None  # what the fit check said, when there was one
    # With prompt caching on: input tokens written to and read from the cache (0 otherwise).
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


def postable(guides: Sequence[Guide]) -> tuple[Guide, ...]:
    """The guides code may ever post: approved by a person, and not flagged by the guide
    screen."""
    return tuple(g for g in guides if g.approved and flag_guide(g) is None)


def entries_for(guides: Sequence[Guide]) -> tuple[Entry, ...]:
    """The list the model sees: each postable guide's id, title and summary, never its steps."""
    return tuple(Entry(id=g.id, title=g.title, summary=g.summary) for g in postable(guides))


def decide(
    pick: Pick,
    approved: frozenset[str],
    screen: str | None,
    *,
    use_screen: bool = True,
    pointer: str | None = None,
) -> tuple[str, str | None, str | None]:
    """(kind, guide, why) for one parsed pick. Fails closed at every step."""
    if pick.off_format:
        return HAND_OFF, None, OFF_FORMAT
    if use_screen and screen is not None:
        return HAND_OFF, None, SCREEN
    if pick.asks_for == ASKS_NOTHING:
        if pointer is not None and pick.guide == pointer and pointer in approved:
            return GUIDE, pointer, None
        return HAND_OFF, None, NO_REQUEST
    if pick.asks_for == ASKS_PERSON:
        return HAND_OFF, None, NEEDS_A_PERSON
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
    mode: str = "plain",
    fit_system: str | None = None,
    pointer: str | None = None,
) -> Outcome:
    """One how-to message from a verified employee: a guide to post, or a hand-off.

    `entries` replaces the guide list the model sees (the eval adds a poisoned guide to it);
    only postable guides (approved, not flagged) can ever be posted, whatever the list holds.

    `mode` is "plain" (one call, as always), "asks" (the reply carries asks_for; `pointer` is
    the guide that may answer a message that asks for nothing) or "fit" (a second call checks
    the chosen guide, with `fit_system`)."""
    if mode not in ("plain", "asks", "fit"):
        raise ValueError(f"unknown picker mode: {mode!r}")
    if mode == "fit" and fit_system is None:
        raise ValueError("mode 'fit' needs a fit_system")
    asks = mode == "asks"
    approved = entries_for(guides)
    entries = approved if entries is None else tuple(entries)
    screen = hand_off_signal(text)
    try:
        response = model.complete(pick_request(text, entries, system, asks=asks))
    except ModelError:
        return Outcome(HAND_OFF, None, MODEL_ERROR, None, screen)
    pick = parse_pick(response, entries, asks=asks)
    kind, guide, why = decide(
        pick,
        frozenset(e.id for e in approved),
        screen,
        use_screen=use_screen,
        pointer=pointer if asks else None,
    )
    tokens_in, tokens_out = response.input_tokens, response.output_tokens
    cache_w, cache_r = response.cache_write_tokens, response.cache_read_tokens
    fit: bool | None = None
    if mode == "fit" and kind == GUIDE:
        assert fit_system is not None  # noqa: S101
        entry = next(e for e in approved if e.id == guide)
        try:
            second = model.complete(fit_request(text, entry, fit_system))
        except ModelError:
            return Outcome(
                HAND_OFF,
                None,
                FIT_FAILED,
                pick,
                screen,
                tokens_in,
                tokens_out,
                None,
                cache_w,
                cache_r,
            )
        tokens_in += second.input_tokens
        tokens_out += second.output_tokens
        cache_w += second.cache_write_tokens
        cache_r += second.cache_read_tokens
        fit = parse_fit(second)
        if fit is None:
            kind, guide, why = HAND_OFF, None, FIT_FAILED
        elif not fit:
            kind, guide, why = HAND_OFF, None, FIT_NO
    return Outcome(kind, guide, why, pick, screen, tokens_in, tokens_out, fit, cache_w, cache_r)


def reply_text(guide: Guide, lang: str = "en") -> str:
    """The reply code posts: the approved steps word for word, and the link."""
    lines = [_HEADERS.get(lang, _HEADERS["en"]), f"*{guide.title}*"]
    lines += [f"{i}. {step}" for i, step in enumerate(guide.steps, 1)]
    lines.append(guide.link)
    return "\n".join(lines)
