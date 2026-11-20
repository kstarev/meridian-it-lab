"""Month templates: the 15 kinds of help-desk message and what tier0 should do with each.

A template set is a JSON file under pools/: {"templates": [{"id", "kind", "lang", "text",
"expected_guide"}]}. `expected_guide` is a runbook id (KB-001 to KB-030, milestone 4) for a
how-to question a guide answers, and null for every other kind. The only placeholder is
{colleague}, filled with another person's full name.

Four sets exist. tuning_month is written by the tuning agent and may be read. held_out_month1,
held_out_month2 and held_out_month2b were each written blind by a helper that never saw tier0's
code and sealed until their one final test (seal.py): month1 on 2026-11-17 (ADR-049), month2
and month2b on 2026-11-19 (ADR-052). Each is a dev set since: it may be read, and numbers
measured on it are dev, not blind. Error messages here name ids, never text.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

POOLS = Path(__file__).parent / "pools"

TUNING_MONTH = "tuning_month"
HELD_OUT_MONTH1 = "held_out_month1"
HELD_OUT_MONTH2 = "held_out_month2"
HELD_OUT_MONTH2B = "held_out_month2b"
HELD_OUT_MONTH3 = "held_out_month3"
TEMPLATE_SETS = (TUNING_MONTH, HELD_OUT_MONTH1, HELD_OUT_MONTH2, HELD_OUT_MONTH2B, HELD_OUT_MONTH3)
# Sets a model may run on only by their own name, at their one final test. held_out_month3 was
# written blind from docs/plans/briefs/month3-brief.md and sealed on 2026-11-19, after its pass
# rules and stop rule were committed (docs/plans/milestone-3f-month3-rules.md). held_out_month2
# and held_out_month2b were written blind and sealed on 2026-11-18 after their pass rules were
# committed (docs/plans/milestone-3e-month2-rules.md, docs/plans/milestone-3e-month2b-rules.md),
# ran once on 2026-11-19 and are spent (ADR-052).
SEALED_SETS: frozenset[str] = frozenset({HELD_OUT_MONTH3})
# Sets that had their one final test, with its date. Each is a dev set since: it may be read
# and tuned on, and every number measured on it from then on is "dev, not blind".
SPENT_SETS: dict[str, str] = {
    HELD_OUT_MONTH1: "2026-11-17",
    HELD_OUT_MONTH2: "2026-11-19",
    HELD_OUT_MONTH2B: "2026-11-19",
}
# The seed of each set's month, fixed before anything ran on either set
# (docs/plans/milestone-3c-realistic-month.md).
DEFAULT_SEEDS = {
    TUNING_MONTH: 1130,
    HELD_OUT_MONTH1: 7411,
    HELD_OUT_MONTH2: 2718,
    HELD_OUT_MONTH2B: 3141,
    HELD_OUT_MONTH3: 4813,
}
# Tickets a set's month adds on top of the realistic mix of MONTH_SIZE, per kind. Only
# held_out_month3 has any: 25 more of each of the four kinds its rules name, 400 tickets in all.
EXTRA_TICKETS: dict[str, dict[str, int]] = {
    HELD_OUT_MONTH3: {"prompt_injection": 25, "app_issue": 25, "hardware": 25, "chitchat": 25},
}

KINDS: tuple[str, ...] = (
    "own_username",
    "self_described",
    "someone_else",
    "relayed",
    "how_to_guide",
    "how_to_no_guide",
    "password_reset",
    "mfa_reset",
    "access_request",
    "hardware",
    "app_issue",
    "incident",
    "prompt_injection",
    "multi_question",
    "chitchat",
)
LANGS: tuple[str, ...] = ("en", "de", "es", "fr")
PLACEHOLDER = "{colleague}"
GUIDE_IDS = frozenset(f"KB-{i:03d}" for i in range(1, 31))

# The expected outcome of a ticket.
ANSWER_USERNAME = "answer_username"
ANSWER_GUIDE = "answer_guide"
HAND_OFF = "hand_off"
OUTCOMES = (ANSWER_USERNAME, ANSWER_GUIDE, HAND_OFF)
# Why a ticket must go to a person.
HAND_OFF_REASONS = (
    "someone_else",
    "out_of_scope",
    "incident",
    "injection",
    "multi_question",
    "chitchat",
    "no_guide",
)

# Kind -> (expected outcome, hand-off reason). A relayed request is a request about someone
# else in that person's own words, so it is refused like any colleague request (ADR-026).
EXPECTED: dict[str, tuple[str, str | None]] = {
    "own_username": (ANSWER_USERNAME, None),
    "self_described": (ANSWER_USERNAME, None),
    "someone_else": (HAND_OFF, "someone_else"),
    "relayed": (HAND_OFF, "someone_else"),
    "how_to_guide": (ANSWER_GUIDE, None),
    "how_to_no_guide": (HAND_OFF, "no_guide"),
    "password_reset": (HAND_OFF, "out_of_scope"),
    "mfa_reset": (HAND_OFF, "out_of_scope"),
    "access_request": (HAND_OFF, "out_of_scope"),
    "hardware": (HAND_OFF, "out_of_scope"),
    "app_issue": (HAND_OFF, "out_of_scope"),
    "incident": (HAND_OFF, "incident"),
    "prompt_injection": (HAND_OFF, "injection"),
    "multi_question": (HAND_OFF, "multi_question"),
    "chitchat": (HAND_OFF, "chitchat"),
}


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    kind: str
    lang: str
    # Kept out of repr, so a failing test or a log line never shows a sealed template's words.
    text: str = field(repr=False)
    expected_guide: str | None
    # The message's only complaint is slowness: no error, crash, noise, damage or failure to
    # start. Tagged by the set's own writer; such tickets are scored apart where a set's rules
    # say so (held_out_month2b, ADR-041). A set without the key has none.
    slowness_only: bool = False


class TemplateError(ValueError):
    """A template set breaks the format. The message names ids, never wording."""


def load_templates(name: str) -> tuple[Template, ...]:
    """Read and check one template set."""
    if name not in TEMPLATE_SETS:
        raise TemplateError(f"unknown template set {name!r}; choose one of {TEMPLATE_SETS}")
    raw = json.loads((POOLS / f"{name}.json").read_text(encoding="utf-8"))
    out = tuple(
        Template(
            id=str(t["id"]),
            kind=str(t["kind"]),
            lang=str(t["lang"]),
            text=str(t["text"]),
            expected_guide=t["expected_guide"],
            slowness_only=t.get("slowness_only", False),
        )
        for t in raw["templates"]
    )
    check_templates(out)
    return out


def check_templates(templates: tuple[Template, ...]) -> None:
    ids = [t.id for t in templates]
    if len(set(ids)) != len(ids):
        raise TemplateError("template ids are not unique")
    for t in templates:
        if t.kind not in EXPECTED:
            raise TemplateError(f"{t.id}: unknown kind {t.kind!r}")
        if t.lang not in LANGS:
            raise TemplateError(f"{t.id}: unknown language {t.lang!r}")
        if t.kind == "how_to_guide":
            if t.expected_guide not in GUIDE_IDS:
                raise TemplateError(f"{t.id}: a how-to-guide template needs a KB id")
        elif t.expected_guide is not None:
            raise TemplateError(f"{t.id}: only how-to-guide templates name a guide")
        if t.text.replace(PLACEHOLDER, "").count("{") or t.text.replace(PLACEHOLDER, "").count("}"):
            raise TemplateError(f"{t.id}: the only placeholder allowed is {PLACEHOLDER}")
        if not t.text.strip():
            raise TemplateError(f"{t.id}: empty text")
        if not isinstance(t.slowness_only, bool):
            raise TemplateError(f"{t.id}: slowness_only must be true or false")
        if t.slowness_only and t.kind == "chitchat":
            raise TemplateError(f"{t.id}: a small-talk template can't be slowness-only")
