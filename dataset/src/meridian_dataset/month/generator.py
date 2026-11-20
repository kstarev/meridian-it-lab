"""The month generator: template x persona x language x Slack noise, seeded.

Everything comes from one random.Random(seed), and the calls happen in a fixed order, so the
same templates and seed always give the same month. The wall clock is never read.

How a month is drawn:

- **Kinds by weight.** KIND_WEIGHTS gives each kind's share of the month. The counts are
  exact (largest remainder), not sampled, so every seed has the same mix: about 12-14%
  own-username requests (with the repeats), and about 70% that must go to a person.
- **Templates within a kind** are shuffled and used in turn, so every template is used about
  equally and none is left out while its kind has tickets to spare.
- **A persona who writes the template's language** sends it, on one of the month's 20
  working days, between 08:00 and 18:00 local time. Nobody sends two own-username requests,
  or two password resets, on the same day by chance: those pairs are only ever planted.
- **Repeat callers** are planted: a few people send a second own-username request, or a
  second password reset, 45 minutes to 4 hours after the first, on the same day.
- **{colleague}** becomes another person's full name.
- **Slack noise** (a greeting, an emoji, a typo) goes on about a quarter of tickets. A
  prompt-injection template's own words are never touched: it can get a greeting before it
  or an emoji after it, never a typo.
"""

import dataclasses
import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from meridian_dataset.month.personas import IT, Persona, build_personas
from meridian_dataset.month.templates import (
    ANSWER_GUIDE,
    EXPECTED,
    KINDS,
    PLACEHOLDER,
    Template,
)

MONTH_GENERATOR_VERSION = "1.0.0"
MONTH_SIZE = 300
MONTH_START = date(2026, 11, 2)  # a Monday
WORKING_DAYS = 20
DAY_START_MINUTE = 8 * 60
DAY_END_MINUTE = 18 * 60

# Each kind's share of the month, in tenths of a percent (they add up to 1000). Only about
# 12% are own-username requests before repeats; most of the rest must go to a person: resets,
# access requests, broken laptops and apps, incidents, injection, colleagues' accounts.
KIND_WEIGHTS: dict[str, int] = {
    "own_username": 95,
    "self_described": 25,
    "someone_else": 35,
    "relayed": 25,
    "how_to_guide": 180,
    "how_to_no_guide": 50,
    "password_reset": 90,
    "mfa_reset": 40,
    "access_request": 150,
    "hardware": 100,
    "app_issue": 80,
    "incident": 20,
    "prompt_injection": 30,
    "multi_question": 40,
    "chitchat": 40,
}
# Planted same-day repeats per 300 tickets: own username again, and password reset again.
USERNAME_REPEATS_PER_300 = 5
PASSWORD_REPEATS_PER_300 = 3
REPEAT_DELAY_MINUTES = (45, 240)
# Kinds that count as "the same request again" when one person sends two on one day.
FAMILIES: dict[str, str] = {
    "own_username": "username",
    "self_described": "username",
    "password_reset": "password",
}
REPEAT_KIND = {"username": "own_username", "password": "password_reset"}

NOISE_RATE = 0.25
GREETING, EMOJI, TYPO = "greeting", "emoji", "typo"
NOISE_KINDS = (TYPO, GREETING, EMOJI)  # also the order they are applied in
# Noise that stays outside a template's own words.
OUTSIDE_NOISE = (GREETING, EMOJI)
GREETINGS: dict[str, tuple[str, ...]] = {
    "en": ("hey 👋 ", "hi! ", "morning :wave: ", "hello IT, ", "hiya, "),
    "de": ("Hallo 👋 ", "Moin, ", "Hi zusammen, ", "Servus! "),
    "es": ("Hola 👋 ", "Buenas, ", "¡Hola! ", "Hola equipo, "),
    "fr": ("Salut 👋 ", "Bonjour, ", "Coucou ! ", "Bonjour l'équipe, "),
}
GREETING_WORDS = frozenset(
    {"hey", "hi", "hello", "hiya", "morning", "hallo", "moin", "servus", "hola", "buenas"}
    | {"bonjour", "salut", "coucou"}
)
EMOJIS = (" 🙏", " :pray:", " :sweat_smile:", " 😅", " :thinking_face:", " 👀", " thx")


@dataclass(frozen=True, slots=True)
class MonthTicket:
    id: str
    template_id: str
    kind: str
    lang: str
    # Kept out of repr, so a failing test never shows a sealed template's words.
    text: str = field(repr=False)
    persona_id: str
    sender_name: str
    sender_email: str
    sent_at: str  # UTC, ISO 8601
    local_day: str  # the sender's own calendar day
    # The answer key: answer_username, answer_guide (with expected_guide) or hand_off (with
    # a reason).
    expected: str
    reason: str | None
    expected_guide: str | None
    # A planted same-day repeat of an earlier ticket by the same person.
    repeat: bool
    repeat_of: str | None
    colleague_id: str | None
    noise: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Month:
    template_set: str
    seed: int
    size: int
    generator_version: str
    personas: tuple[Persona, ...]
    tickets: tuple[MonthTicket, ...]


@dataclass(slots=True)
class _Draft:
    template: Template
    persona: Persona
    day: int
    minute: int
    repeat_of: int | None = None


def kind_counts(present: Sequence[str], base: int) -> dict[str, int]:
    """Exact ticket counts per kind for `base` tickets, by largest remainder. Kinds missing
    from the template set get none, and their share goes to the others."""
    kinds = [k for k in KINDS if k in set(present)]
    total = sum(KIND_WEIGHTS[k] for k in kinds)
    raw = {k: base * KIND_WEIGHTS[k] for k in kinds}
    counts = {k: raw[k] // total for k in kinds}
    left = base - sum(counts.values())
    by_remainder = sorted(kinds, key=lambda k: (-(raw[k] % total), KINDS.index(k)))
    for k in by_remainder[:left]:
        counts[k] += 1
    return counts


def _working_day(index: int) -> date:
    return MONTH_START + timedelta(days=7 * (index // 5) + index % 5)


def _typo(rng: random.Random, text: str) -> str | None:
    """Swap two neighbouring letters inside one word of 5+ letters; None if no word fits.
    Placeholders, links, addresses and numbers are never touched."""
    words = text.split(" ")
    fits = [i for i, w in enumerate(words) if w.isalpha() and len(w) >= 5]
    if not fits:
        return None
    i = rng.choice(fits)
    word = words[i]
    # Keep the first and last letters; swap two that differ, so the typo is real.
    spots = [j for j in range(1, len(word) - 2) if word[j] != word[j + 1]]
    if not spots:
        return None
    j = rng.choice(spots)
    words[i] = word[:j] + word[j + 1] + word[j] + word[j + 2 :]
    return " ".join(words)


def _starts_with_greeting(text: str) -> bool:
    first = text.split(maxsplit=1)[0] if text.split() else ""
    return first.strip(",.!¡?¿:;").lower() in GREETING_WORDS


def _noisy_text(
    rng: random.Random, draft: _Draft, others: Sequence[Persona]
) -> tuple[str, str | None, tuple[str, ...]]:
    """The ticket text, the colleague it names (if any) and the noise applied."""
    template = draft.template
    kinds: list[str] = []
    if rng.random() < NOISE_RATE:
        allowed = OUTSIDE_NOISE if template.kind == "prompt_injection" else NOISE_KINDS
        how_many = 1 if rng.random() < 0.7 else 2
        kinds = sorted(rng.sample(allowed, how_many), key=NOISE_KINDS.index)
    text = template.text
    applied: list[str] = []
    if TYPO in kinds:
        changed = _typo(rng, text)
        if changed is not None:
            text = changed
            applied.append(TYPO)
    colleague: Persona | None = None
    if PLACEHOLDER in text:
        colleague = rng.choice(others)
        text = text.replace(PLACEHOLDER, colleague.full_name)
    if GREETING in kinds and not _starts_with_greeting(text):
        text = rng.choice(GREETINGS[template.lang]) + text
        applied.append(GREETING)
    if EMOJI in kinds:
        text += rng.choice(EMOJIS)
        applied.append(EMOJI)
    return text, colleague.id if colleague else None, tuple(applied)


def build_month(
    templates: Sequence[Template],
    *,
    seed: int,
    size: int = MONTH_SIZE,
    name: str = "custom",
    personas: Sequence[Persona] | None = None,
    extra: Mapping[str, int] | None = None,
) -> Month:
    """About `size` tickets of a 230-person company's month, from these templates, plus `extra`
    tickets of the kinds it names on top of the mix (held_out_month3)."""
    rng = random.Random(seed)
    people = tuple(personas) if personas is not None else build_personas()
    senders: dict[str, list[Persona]] = {}
    for p in people:
        if p.department != IT:
            senders.setdefault(p.language, []).append(p)
    missing = sorted({t.lang for t in templates} - set(senders))
    if missing:
        raise ValueError(f"no persona writes {', '.join(missing)}")

    kinds_present = {t.kind for t in templates}
    repeats_wanted = {
        family: (size * per_300 + 150) // 300 if REPEAT_KIND[family] in kinds_present else 0
        for family, per_300 in (
            ("username", USERNAME_REPEATS_PER_300),
            ("password", PASSWORD_REPEATS_PER_300),
        )
    }
    base = size - sum(repeats_wanted.values())
    counts = kind_counts([t.kind for t in templates], base)
    for kind, more in (extra or {}).items():
        if kind not in counts:
            raise ValueError(f"extra tickets for a kind the set doesn't have: {kind}")
        counts[kind] += more

    picks: list[Template] = []
    for kind in KINDS:
        pool = [t for t in templates if t.kind == kind]
        if not pool:
            continue
        rng.shuffle(pool)
        picks += [pool[i % len(pool)] for i in range(counts[kind])]

    drafts: list[_Draft] = []
    taken: set[tuple[str, int, str]] = set()
    for template in picks:
        family = FAMILIES.get(template.kind)
        for _ in range(1000):
            persona = rng.choice(senders[template.lang])
            day = rng.randrange(WORKING_DAYS)
            if family is None or (persona.id, day, family) not in taken:
                break
        else:  # pragma: no cover - 1000 clashes in a row can't happen at these sizes
            raise RuntimeError("could not place a ticket without an unplanted repeat")
        if family is not None:
            taken.add((persona.id, day, family))
        minute = rng.randrange(DAY_START_MINUTE, DAY_END_MINUTE)
        drafts.append(_Draft(template, persona, day, minute))

    for family in ("username", "password"):
        repeat_kind = REPEAT_KIND[family]
        candidates = [
            i
            for i, d in enumerate(drafts)
            if FAMILIES.get(d.template.kind) == family
            and any(t.kind == repeat_kind and t.lang == d.template.lang for t in templates)
        ]
        rng.shuffle(candidates)
        chosen: list[int] = []
        senders_chosen: set[str] = set()
        for i in candidates:
            if len(chosen) == repeats_wanted[family]:
                break
            if drafts[i].persona.id not in senders_chosen:
                chosen.append(i)
                senders_chosen.add(drafts[i].persona.id)
        for i in chosen:
            first = drafts[i]
            options = [
                t for t in templates if t.kind == repeat_kind and t.lang == first.template.lang
            ]
            different = [t for t in options if t.id != first.template.id] or options
            delay = rng.randint(*REPEAT_DELAY_MINUTES)
            drafts.append(
                _Draft(rng.choice(different), first.persona, first.day, first.minute + delay, i)
            )

    built: list[tuple[str, int, _Draft, str, str | None, tuple[str, ...]]] = []
    for index, draft in enumerate(drafts):
        others = [p for p in people if p.id != draft.persona.id]
        text, colleague_id, noise = _noisy_text(rng, draft, others)
        local = datetime.combine(_working_day(draft.day), datetime.min.time()) + timedelta(
            minutes=draft.minute
        )
        utc = local - timedelta(minutes=draft.persona.utc_offset_minutes)
        built.append((utc.strftime("%Y-%m-%dT%H:%M:%SZ"), index, draft, text, colleague_id, noise))
    built.sort(key=lambda b: (b[0], b[1]))
    ids = {index: f"MON-{n:03d}" for n, (_, index, *_rest) in enumerate(built, 1)}

    tickets = []
    for sent_at, index, draft, text, colleague_id, noise in built:
        t, p = draft.template, draft.persona
        expected, reason = EXPECTED[t.kind]
        tickets.append(
            MonthTicket(
                id=ids[index],
                template_id=t.id,
                kind=t.kind,
                lang=t.lang,
                text=text,
                persona_id=p.id,
                sender_name=p.full_name,
                sender_email=p.email,
                sent_at=sent_at,
                local_day=_working_day(draft.day).isoformat(),
                expected=expected,
                reason=reason,
                expected_guide=t.expected_guide if expected == ANSWER_GUIDE else None,
                repeat=draft.repeat_of is not None,
                repeat_of=ids[draft.repeat_of] if draft.repeat_of is not None else None,
                colleague_id=colleague_id,
                noise=noise,
            )
        )
    return Month(
        template_set=name,
        seed=seed,
        size=len(tickets),
        generator_version=MONTH_GENERATOR_VERSION,
        personas=people,
        tickets=tuple(tickets),
    )


def month_digest(month: Month) -> str:
    """SHA-256 of the month's canonical JSON: pins the generator's output in tests."""
    blob = json.dumps(
        dataclasses.asdict(month), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
