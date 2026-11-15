"""The realistic month (milestone 3c): about 300 help-desk tickets for a 230-person company.

A month is generated as template x persona x language x Slack noise, seeded, so the same
templates and seed always give the same tickets. Most tickets must go to a person (resets,
access requests, laptops, incidents, prompt injection, colleagues' accounts); only about
12-14% are own-username requests. Each ticket's answer key says what tier0 should do:
answer with the sender's username, answer with one guide (a KB id), or hand off, with a
reason. Every ticket records its template, so intervals can resample templates.

    templates.py  the 15 kinds, the expected outcome of each, and loading a template set
    personas.py   the 230 people who send tickets
    generator.py  build_month(): the seeded month
    seal.py       the SHA-256 seal on the blind set, held_out_month1
"""

from meridian_dataset.month.generator import (
    MONTH_GENERATOR_VERSION,
    MONTH_SIZE,
    Month,
    MonthTicket,
    build_month,
    kind_counts,
    month_digest,
)
from meridian_dataset.month.personas import Persona, build_personas
from meridian_dataset.month.templates import (
    ANSWER_GUIDE,
    ANSWER_USERNAME,
    DEFAULT_SEEDS,
    EXPECTED,
    HAND_OFF,
    HAND_OFF_REASONS,
    HELD_OUT_MONTH1,
    KINDS,
    LANGS,
    SEALED_SETS,
    TEMPLATE_SETS,
    TUNING_MONTH,
    Template,
    TemplateError,
    load_templates,
)


def month_for(name: str, *, seed: int | None = None, size: int = MONTH_SIZE) -> Month:
    """The month of a named template set, at its fixed seed unless another is given."""
    return build_month(
        load_templates(name),
        seed=DEFAULT_SEEDS[name] if seed is None else seed,
        size=size,
        name=name,
    )


__all__ = [
    "ANSWER_GUIDE",
    "ANSWER_USERNAME",
    "DEFAULT_SEEDS",
    "EXPECTED",
    "HAND_OFF",
    "HAND_OFF_REASONS",
    "HELD_OUT_MONTH1",
    "KINDS",
    "LANGS",
    "MONTH_GENERATOR_VERSION",
    "MONTH_SIZE",
    "SEALED_SETS",
    "TEMPLATE_SETS",
    "TUNING_MONTH",
    "Month",
    "MonthTicket",
    "Persona",
    "Template",
    "TemplateError",
    "build_month",
    "build_personas",
    "kind_counts",
    "load_templates",
    "month_digest",
    "month_for",
]
