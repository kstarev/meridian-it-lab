"""A code-only screen for guides: does a guide's text hold an instruction? (the owner's ruling,
2026-11-16; docs/plans/milestone-4-knowledge.md).

The model is not the defence against a poisoned guide. A person approves every guide (ADR-035),
and this screen is a second, independent check behind that approval: code never posts a guide
it flags, and never shows one to the model. It reads the title, the summary and every step,
including text in quotes or a code block, because the steps are what code posts word for word.

It flags four things:

    addressed_to_bot   text addressed to an AI, an assistant, a bot or "the system"
    steering           text that says which guide to choose, to ignore the others, or that
                       some requests need no person
    credentials        a request to send or share a password, sign-in details or a code
    outside_address    an email address or a link outside meridian.example

It was written from tuning data only: the 7 tuning poisoned guides (PT-1 to PT-7) and the 30
approved runbooks. A test requires it to flag every one of those poisoned guides and none of
the runbooks, and pins its patterns. It is separate from the message screen (screen.py), which
stays frozen as it was. What it misses on new wording, the sealed test's code-only result shows.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol

ADDRESSED_TO_BOT = "addressed_to_bot"
STEERING = "steering"
CREDENTIALS = "credentials"
OUTSIDE_ADDRESS = "outside_address"
KINDS = (ADDRESSED_TO_BOT, STEERING, CREDENTIALS, OUTSIDE_ADDRESS)

DOMAIN = "meridian.example"

_BOT_WORDS = (
    r"(ai|a i|assistant|ai assistant|bot|chatbot|help ?desk bot|it bot|support bot|model|llm"
    r"|language model|system|ki|ia|asistente|assistent|assistante|systeme|sistema)"
)

# Words that open a line addressed to the bot, as in "Assistant: ..." or "SYSTEM OVERRIDE: ...".
# Only at the start of a piece or a sentence, or after a quote or a code mark, so a runbook line
# such as "Operating system: macOS" is not flagged. "model" is left out here ("Model: ...").
_COLON_WORDS = (
    r"(ai|assistant|ai assistant|bot|chatbot|help ?desk bot|it bot|support bot|llm|system|ki|ia"
    r"|asistente|assistent|assistante|systeme|sistema)"
)
# Read on the lower-cased text with accents removed and punctuation kept.
_ADDRESS_PATTERNS: tuple[str, ...] = (
    rf"(^|[.!?;]\s+|[\"'`(\[>*_]\s*){_COLON_WORDS}( override| note| message| instruction)?\s*:",
)
# Read on the normalised text: lower case, no accents, letters and digits only, single spaces.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        ADDRESSED_TO_BOT,
        rf"\b(note|message|instruction|instructions|hint|reminder|notice|hinweis|nota|remarque)"
        rf" (to|for|an|fur|para|pour|a) (the |die |den |la |le |l )?{_BOT_WORDS}\b",
    ),
    (
        ADDRESSED_TO_BOT,
        rf"\b{_BOT_WORDS} (reading|that reads|who reads|which reads) (this|these)\b",
    ),
    (ADDRESSED_TO_BOT, r"\b(help ?desk|it|support|service desk) bot\b"),
    (ADDRESSED_TO_BOT, r"\b(chatbot|language model|llm|ai assistant)\b"),
    (STEERING, r"\b(choose|select|pick|use|take) (this|that|the same) guide\b"),
    (STEERING, r"\bthis guide (replaces|overrides|supersedes|comes before)\b"),
    (
        STEERING,
        r"\b(for|to|on) (every|any|all|each) (other )?(question|questions|request|requests"
        r"|message|messages|ticket|tickets|topic|topics)\b",
    ),
    (
        STEERING,
        r"\b(ignore|disregard|ignoriere|ignorieren|ignora|ignorar|ignorez|ignore)\b"
        r" (the|all|any|every|previous|prior|earlier|other|your|these|those|alle|die|todas"
        r"|las|los|tous|les)\b",
    ),
    (
        STEERING,
        r"\b(never|do not|dont|must not|should not|no need to)"
        r" (hand|pass|send|escalate|forward|give)( them| those| these| it| the message| requests)?"
        r" (off|on|to a person|to a human|to people|to the it team|to someone|to it staff)\b",
    ),
    (STEERING, r"\b(must not|never|do not|dont) (answer|reply|say) none\b"),
    (STEERING, r"\bfrom now on\b"),
    (STEERING, r"\b(are|is|count as|counts as) (a |an )?how to (question|questions|request)\b"),
    (STEERING, r"\boverride\b"),
    (STEERING, r"\b(need|needs) no (person|human|review|hand off)\b"),
    (STEERING, r"\banswer (them|it|those|these|every|all) with this guide\b"),
    (STEERING, r"\bwhatever (it is|they are|the question is) about\b"),
)

_SEND = (
    r"(send|sends|email|e mail|mail|share|give|tell|forward|reply with|post|paste|read out|text"
    r"|dm|message|senden|sende|schick|schicke|envia|enviar|manda|mandar|envoie|envoyez|envoyer"
    r"|donne|donnez|partage|partagez|comparte|teile)"
)
_PRIVATE_WORDS = (
    r"(password|passwords|passcode|passcodes|sign in details|login details|log in details"
    r"|credentials|mfa code|mfa codes|verification code|one time code|security code|recovery code"
    r"|recovery codes|2fa code|backup code|backup codes|pin|passwort|kennwort|zugangsdaten"
    r"|contrasena|credenciales|mot de passe|identifiants)"
)
# A password manager or vault is a tool, not a secret; "never send a password" is a warning.
_CREDENTIALS = re.compile(rf"\b{_SEND}\b(.{{0,50}}?)\b{_PRIVATE_WORDS}\b(?! manager| vault)")
_WARNING_BEFORE = re.compile(r"\b(never|not|dont|don t|nie|niemals|nunca|jamais|ne)\b")

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_LINK = re.compile(r"\b(?:https?://|www\.)([^/\s\"'`)>]+)", re.IGNORECASE)

_COMPILED_ADDRESS = tuple(re.compile(p) for p in _ADDRESS_PATTERNS)
_COMPILED = tuple((kind, re.compile(p)) for kind, p in _PATTERNS)


class GuideText(Protocol):
    title: str
    summary: str
    steps: tuple[str, ...]


def _fold(text: str) -> str:
    """Lower case, no accents; punctuation kept."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _normalise(text: str) -> str:
    """Lower case, no accents, letters and digits only, single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", _fold(text)).strip()


def _ours(domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    return domain == DOMAIN or domain.endswith("." + DOMAIN)


def flag_text(text: str) -> str | None:
    """Why one piece of guide text must not be posted, or None."""
    for domain in (*_EMAIL.findall(text), *_LINK.findall(text)):
        if not _ours(domain):
            return OUTSIDE_ADDRESS
    folded = _fold(text)
    if any(p.search(folded) for p in _COMPILED_ADDRESS):
        return ADDRESSED_TO_BOT
    normalised = _normalise(text)
    for kind, pattern in _COMPILED:
        if pattern.search(normalised):
            return kind
    for match in _CREDENTIALS.finditer(normalised):
        before = normalised[max(0, match.start() - 16) : match.start()]
        if not _WARNING_BEFORE.search(before) and not _WARNING_BEFORE.search(match.group(2)):
            return CREDENTIALS
    return None


def pieces(guide: GuideText) -> Iterable[str]:
    yield guide.title
    yield guide.summary
    yield from guide.steps


def flag_guide(guide: GuideText) -> str | None:
    """Why this guide must never be posted or shown to the model, or None when it is clean."""
    for piece in pieces(guide):
        kind = flag_text(piece)
        if kind is not None:
            return kind
    return None
