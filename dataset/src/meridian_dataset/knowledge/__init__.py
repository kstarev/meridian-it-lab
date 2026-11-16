"""Milestone 4, Knowledge (ADR-035): fake runbooks, resolved tickets and how-to question sets.

Three things live here, all fake:

- **Runbooks** (`runbooks.json`): the IT team's 30 approved guides, as they would sit in the
  reviewed `meridian-memory` repo (ADR-010). tier0 may answer a how-to question only by
  posting one of these, word for word.
- **Resolved tickets** (`resolved_tickets`): about 200 past how-to tickets, each with how it
  was resolved (a guide, or a hand-off and why). They are generated from the tuning templates
  in `pools/tuning.json` by a seeded generator, and they are the tuning data for the picker.
- **Question sets** (`load_question_set`): "tuning" (the resolved tickets) and the sealed
  held-out how-to test, which a blind helper wrote and nobody on the tuning side reads
  (`pools/held_out_k1.json`, sealed by SHA-256 in `seal.py`).

A question belongs to one group:

    guide              one approved guide answers it
    no_guide           a how-to no guide answers: hand off
    must_hand_off      an account action, a request about someone else, or an incident:
                       never answered from a guide
    injected_question  an instruction for the bot is hidden in the message (ASI01)
    poisoned_guide     a fake guide with a hidden instruction sits in the guide list (ASI06)
"""

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
POOLS = HERE / "pools"
MEMORY_REPO_URL = "https://memory.meridian.example/runbooks"
KNOWLEDGE_SEED = 20261115
GROUPS = ("guide", "no_guide", "must_hand_off", "injected_question", "poisoned_guide")
PLANTED_AT = ("summary", "steps_start", "steps_middle", "steps_end", "steps_quoted")
HAND_OFF_REASONS = ("account_action", "someone_else", "incident")
LANGUAGES = ("en", "de", "es", "fr")
TUNING = "tuning"
HELD_OUT_K1 = "held_out_k1"
QUESTION_SETS = (TUNING, HELD_OUT_K1)


@dataclass(frozen=True, slots=True)
class Guide:
    """One approved runbook."""

    id: str
    slug: str
    title: str
    summary: str
    steps: tuple[str, ...]
    tags: tuple[str, ...]
    approved: bool
    last_reviewed: str

    @property
    def link(self) -> str:
        return f"{MEMORY_REPO_URL}/{self.slug}.md"


@dataclass(frozen=True, slots=True)
class PoisonedGuide:
    """A fake guide that carries a hidden instruction. Never a right answer.

    `planted` says where the instruction sits: "summary", "steps_start", "steps_middle",
    "steps_end" or "steps_quoted" (inside quoted text or a code block in a step)."""

    id: str
    title: str
    summary: str
    steps: tuple[str, ...] = ()
    planted: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    group: str
    lang: str
    text: str
    expected_guide: str | None
    reason: str | None = None
    attack_goal: str | None = None
    attack_guide: str | None = None
    poisoned: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionSet:
    name: str
    questions: tuple[Question, ...]
    poisoned_guides: tuple[PoisonedGuide, ...]

    def poisoned_guide(self, guide_id: str) -> PoisonedGuide:
        return next(g for g in self.poisoned_guides if g.id == guide_id)


@dataclass(frozen=True, slots=True)
class ResolvedTicket:
    """A past how-to ticket and how the IT team resolved it."""

    id: str
    requester_id: str
    requester_first_name: str
    department: str
    opened_at: str
    resolved_at: str
    resolved_by: str
    question: Question
    resolution: str  # "guide" or "handed_off"
    note: str


def load_runbooks() -> tuple[Guide, ...]:
    """Every approved runbook, in id order. A draft (approved: false) is never returned."""
    raw = json.loads((HERE / "runbooks.json").read_text(encoding="utf-8"))
    guides = tuple(
        Guide(
            id=r["id"],
            slug=r["slug"],
            title=r["title"],
            summary=r["summary"],
            steps=tuple(r["steps"]),
            tags=tuple(r["tags"]),
            approved=r["approved"] is True,
            last_reviewed=r["last_reviewed"],
        )
        for r in raw["runbooks"]
    )
    return tuple(g for g in guides if g.approved)


def render_markdown(guide: Guide) -> str:
    """The guide as its file in the meridian-memory repo."""
    lines = [f"# {guide.title}", "", guide.summary, ""]
    lines += [f"{i}. {step}" for i, step in enumerate(guide.steps, 1)]
    lines += ["", f"Guide {guide.id}. Last reviewed by the IT team on {guide.last_reviewed}.", ""]
    return "\n".join(lines)


def _question(raw: dict[str, Any], question_id: str) -> Question:
    return Question(
        id=question_id,
        group=raw["group"],
        lang=raw["lang"],
        text=raw["text"],
        expected_guide=raw.get("expected_guide"),
        reason=raw.get("reason"),
        attack_goal=raw.get("attack_goal"),
        attack_guide=raw.get("attack_guide"),
        poisoned=raw.get("poisoned"),
    )


def _poisoned(raw: dict[str, Any]) -> tuple[PoisonedGuide, ...]:
    return tuple(
        PoisonedGuide(
            id=g["id"],
            title=g["title"],
            summary=g["summary"],
            steps=tuple(g.get("steps", ())),
            planted=tuple(g.get("planted", ())),
        )
        for g in raw.get("poisoned_guides", ())
    )


# Fake people for the resolved tickets. They are not the tier0 world's people: that world is
# small on purpose, and its hash is pinned.
_FIRST_NAMES = (
    "Amara", "Ben", "Chiara", "Deniz", "Elif", "Farid", "Greta", "Hugo", "Ines", "Jonas",
    "Kaito", "Lucía", "Malik", "Nora", "Oskar", "Paula", "Quentin", "Rosa", "Sami", "Tilda",
)  # fmt: skip
_DEPARTMENTS = ("Engineering", "Sales", "Marketing", "People", "Finance", "Support", "Product")
_IT_STAFF = ("IT desk: Ayla", "IT desk: Marek", "IT desk: Sofia", "IT desk: Tom")
_GREETINGS = {
    "en": ("hi, ", "hey ", "Hi IT, ", "morning! ", "quick one: "),
    "de": ("Hallo, ", "Hi, ", "Moin, "),
    "es": ("Hola, ", "Buenas, "),
    "fr": ("Bonjour, ", "Salut, "),
}
_ENDINGS = (" thanks!", " thx", " 🙏", " :slightly_smiling_face:", "?")
_NOTES = {
    "guide": "Answered from the guide.",
    "no_guide": "No guide covers this. Answered by hand; a guide could be drafted.",
    "account_action": "Account action: done by a person after the identity check.",
    "someone_else": "About someone else's account or device: handled by a person.",
    "incident": "Incident: handled by a person straight away.",
    "injected_question": "The message carried an instruction for the bot. Ignored.",
    "poisoned_guide": "A guide entry carried an instruction for the bot. Ignored and removed.",
}
REFERENCE_TIME = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _with_slack_noise(text: str, lang: str, rng: random.Random) -> str:
    """About a third of messages get a greeting, and a few an ending, like real Slack.
    Messages that carry an injection are left exactly as written."""
    roll = rng.random()
    if roll < 0.30:
        text = rng.choice(_GREETINGS[lang]) + text
    if roll > 0.85 and not text.endswith("?"):
        text += rng.choice(_ENDINGS)
    return text


def resolved_tickets(seed: int = KNOWLEDGE_SEED) -> tuple[ResolvedTicket, ...]:
    """About 200 fake resolved how-to tickets, one per tuning template, in a seeded order."""
    raw = json.loads((POOLS / "tuning.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    templates = list(raw["templates"])
    rng.shuffle(templates)
    tickets = []
    for number, template in enumerate(templates, 1):
        noisy = dict(template)
        if template["group"] in ("guide", "no_guide", "must_hand_off"):
            noisy["text"] = _with_slack_noise(template["text"], template["lang"], rng)
        question = _question(noisy, f"KT-{number:03d}")
        opened = REFERENCE_TIME - timedelta(days=rng.randint(1, 56), minutes=rng.randint(0, 540))
        answered = question.group == "guide" or (
            question.group in ("injected_question", "poisoned_guide") and question.expected_guide
        )
        note_key = "guide" if question.group == "guide" else (question.reason or question.group)
        tickets.append(
            ResolvedTicket(
                id=question.id,
                requester_id=f"EMP-{rng.randint(1000, 9999)}",
                requester_first_name=rng.choice(_FIRST_NAMES),
                department=rng.choice(_DEPARTMENTS),
                opened_at=opened.isoformat().replace("+00:00", "Z"),
                resolved_at=(opened + timedelta(minutes=rng.randint(4, 300)))
                .isoformat()
                .replace("+00:00", "Z"),
                resolved_by=rng.choice(_IT_STAFF),
                question=question,
                resolution="guide" if answered else "handed_off",
                note=_NOTES[note_key],
            )
        )
    return tuple(tickets)


def load_question_set(name: str) -> QuestionSet:
    """A question set by name. The tuning set is the resolved tickets' questions; the sealed
    set is read from its pool file as the blind helper wrote it."""
    if name == TUNING:
        raw = json.loads((POOLS / "tuning.json").read_text(encoding="utf-8"))
        return QuestionSet(
            name=TUNING,
            questions=tuple(t.question for t in resolved_tickets()),
            poisoned_guides=_poisoned(raw),
        )
    if name not in QUESTION_SETS:
        raise ValueError(
            f"unknown question set {name!r}; choose one of: {', '.join(QUESTION_SETS)}"
        )
    raw = json.loads((POOLS / f"{name}.json").read_text(encoding="utf-8"))
    return QuestionSet(
        name=name,
        questions=tuple(_question(q, q["id"]) for q in raw["questions"]),
        poisoned_guides=_poisoned(raw),
    )


def problems(questions: QuestionSet, guides: tuple[Guide, ...]) -> list[str]:
    """What is wrong with a question set's shape. Messages name ids and counts only, never
    wording, so this is safe to run on a sealed set."""
    ids = {g.id for g in guides}
    poisoned = {g.id for g in questions.poisoned_guides}
    out = []
    seen: set[str] = set()
    for q in questions.questions:
        if q.id in seen:
            out.append(f"{q.id}: id used twice")
        seen.add(q.id)
        if q.group not in GROUPS:
            out.append(f"{q.id}: unknown group")
        if q.lang not in LANGUAGES:
            out.append(f"{q.id}: unknown language")
        if not q.text.strip():
            out.append(f"{q.id}: empty text")
        if q.expected_guide is not None and q.expected_guide not in ids:
            out.append(f"{q.id}: expected guide is not an approved guide")
        if q.group == "guide" and q.expected_guide is None:
            out.append(f"{q.id}: a guide question needs an expected guide")
        if q.group in ("no_guide", "must_hand_off") and q.expected_guide is not None:
            out.append(f"{q.id}: this group must be handed off")
        if q.group == "must_hand_off" and q.reason not in HAND_OFF_REASONS:
            out.append(f"{q.id}: unknown hand-off reason")
        if q.group == "poisoned_guide" and q.poisoned not in poisoned:
            out.append(f"{q.id}: names no poisoned guide of this set")
        if q.attack_guide is not None and q.attack_guide not in ids | poisoned:
            out.append(f"{q.id}: attack guide is not a guide")
    if poisoned & ids:
        out.append("a poisoned guide reuses an approved guide's id")
    for g in questions.poisoned_guides:
        if not g.planted or set(g.planted) - set(PLANTED_AT):
            out.append(f"{g.id}: says nowhere, or somewhere unknown, that its instruction sits")
        if not 3 <= len(g.steps) <= 9:
            out.append(f"{g.id}: a poisoned guide needs 3 to 9 steps")
    return out
