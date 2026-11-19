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

The sealed how-to test (R1, 2026-11-16) found one miss: an instruction in quoted text or a code
block inside a step. The fix (docs/plans/milestone-4-guide-screen-fix.md) adds patterns beside
the old ones, which are kept as they were. They were written from 12 more tuning poisoned guides
(PT-8 to PT-19), all with the instruction in quoted text or a code block, and checked against the
12 clean tuning guides (CT-1 to CT-12). They cover:
- a line or a quote addressed to an AI after any mark or line break, and tags such as "[bot]";
- "if you are an AI", "AI tools that read this page", "AI instructions";
- more steering wording, some of it in German, Spanish or French;
- more ways to ask for a code.
`held_out_g2`, written blind, measures it.

Property tests (2026-11-18) found ways past it that need no new wording: a link whose host is
followed by "?", "#" or "\", and look-alike or hidden characters (mathematical bold capitals, a
Cyrillic letter, a zero-width character inside a word). The fix leaves every pattern as it was:
the screen also reads each link's host up to those marks, and reads the text again in the forms
lookalikes.py makes. Both can only add flags.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol

from tier0.knowledge import lookalikes

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
# Words for an AI reader only, without "system" or "model", which runbooks use for other things.
# Added for the fix to the miss on quoted text and code blocks, from PT-8 to PT-19 only.
_AI_WORDS = (
    r"(ai|a i|ki|ia|assistants?|ai assistants?|bots?|chatbots?|help ?desk bots?|it bots?"
    r"|support bots?|helper bots?|llms?|language models?|copilots?|virtual (agents?|assistants?)"
    r"|ai (agents?|tools?|helpers?|systems?|models?)|agents? ia|ki (assistent(en)?|agent(en)?)"
    r"|automated (helpers?|readers?|assistants?|tools?|agents?|systems?)"
    r"|automatische (assistenten|helfer)|asistentes?( virtual(es)?)?|assistenten?"
    r"|assistantes?( virtuel(le)?s?)?|summari[sz]ers?|crawlers?|scrapers?)"
)
# The same, as the head of a line or a quote addressed to an AI ("helper bots:", "# copilot:").
_LINE_WORDS = (
    r"(ai|assistants?|ai assistants?|bots?|helper bots?|chatbots?|help ?desk bots?|it bots?"
    r"|support bots?|llms?|ki|ia|copilot|virtual (agent|assistant)|ai agent|agent ia"
    r"|ki assistent(en)?|asistentes?( virtual)?|assistent(en)?|assistante( virtuelle)?"
    r"|automation|automated assistant)"
)
# Read on the lower-cased text with accents removed and punctuation kept.
_ADDRESS_PATTERNS: tuple[str, ...] = (
    rf"(^|[.!?;]\s+|[\"'`(\[>*_]\s*){_COLON_WORDS}( override| note| message| instruction)?\s*:",
    # At the start of any line, or after any mark (a comment sign, a quote, a code mark).
    rf"(?m)(^|[^\w\s]\s*){_LINE_WORDS}( override| note| message| instructions?| directives?"
    rf"| update| prompt)?\s*:",
    # A tag such as "[bot]" or "(automation)".
    r"[\[(<{]\s*(bots?|automation|assistant|llm|copilot|chatbot|ai assistant)\s*[\])>}]",
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
    # Added for the fix to the miss on quoted text and code blocks, from PT-8 to PT-19 only.
    (ADDRESSED_TO_BOT, rf"\b(if|when) you (are|re) (an? |the )?{_AI_WORDS}\b"),
    (
        ADDRESSED_TO_BOT,
        rf"\b{_AI_WORDS} (that |who |which |qui |que |die |der )?(read|reads|reading|summari[sz]e"
        r"|summari[sz]es|summari[sz]ing|process|processes|processing|parse|parses|parsing|lit"
        r"|lisent|lee|leen|liest|lesen) (this|these|the|cette|ce|cet|ces|esta|este|diese|dieser"
        r"|dieses)\b",
    ),
    (
        ADDRESSED_TO_BOT,
        rf"\b(note|notes|message|instruction|instructions|hint|reminder|notice|directive|rule"
        rf"|rules|hinweis|nota|remarque|consigne) (to|for|an|fur|para|pour|a) (the |all |any "
        rf"|die |den |la |le |l |los |las |les )?{_AI_WORDS}\b",
    ),
    (
        ADDRESSED_TO_BOT,
        rf"\b{_AI_WORDS} (directive|directives|instruction|instructions|prompt|prompts|override"
        r"|overrides)\b",
    ),
    (STEERING, r"\b(choose|select|pick|use|take) (this|that|the same) runbook\b"),
    (
        STEERING,
        r"\b(rank|prefer|recommend|list|surface) (this|that) (guide|runbook|page|article)\b",
    ),
    (STEERING, r"\bthis (guide|runbook|page|article) (settles|is the answer to)\b"),
    (
        STEERING,
        r"\b(answers|settles|handles|resolves) (every|any|all|each) (\w+ ){0,4}?(question"
        r"|questions|request|requests|message|messages|ticket|tickets|topic|topics)\b",
    ),
    (
        STEERING,
        r"\b(skip|bypass|without|ohne|sin|sans) (the |a |any |el |la |le |l |die |den |das |los "
        r"|las |les )?(it team|it desk|it staff|service desk|help desk|hand off|handoff|human"
        r"|humans|escalation|equipo de it|equipe it)\b",
    ),
    (STEERING, r"\b(keep|leave) (humans|people|the it team|a person|staff) out of\b"),
    (
        STEERING,
        r"\b(never|do not|dont|must not|should not|no need to) route( them| those| these| it"
        r"| requests)? (to a person|to a human|to people|to the it team|to someone|to it staff)\b",
    ),
    (STEERING, r"\bno (escalation|hand off|handoff|human|person) (needed|required|necessary)\b"),
    (STEERING, r"\b(are|is) (fine|ok|okay|safe|allowed) to (answer|share|give|reply to|approve)\b"),
    (
        STEERING,
        r"\btreat (them|it|these|those|this|every|each|all|any)\b.{0,40}?\b(as|like) (the senders"
        r" own|the sender s own|their own|already approved|verified)\b",
    ),
    (
        STEERING,
        r"\b(mfa|password|passwort|account|sign in|login) (reset|resets|unlock|unlocks)\b.{0,20}?"
        r"\b(for|to) (anyone|anybody|everyone|whoever)\b",
    ),
    (
        STEERING,
        r"\bnicht (an |zur |zum )?(die |den |das )?(it|it abteilung|it team|support|service desk"
        r"|helpdesk|menschen|person|mitarbeiter) weiter(leiten|geben)\b",
    ),
    (STEERING, r"\b(direkt|selbst) (\w+ ){0,2}?beantworten\b"),
    (
        STEERING,
        r"\b(sin|no) (pasarlas|pasarlos|pasarla|pasarlo|las pases|los pases|reenviarlas"
        r"|reenviarlos|escalarlas|escalarlos)\b",
    ),
    (
        STEERING,
        r"\b(sans|ne) (les |le |la |l )?(transmettre|transmettez|transferer|transferez"
        r"|escalader)\b",
    ),
)

_SEND = (
    r"(send|sends|email|e mail|mail|share|give|tell|forward|reply with|post|paste|read out|text"
    r"|dm|message|senden|sende|schick|schicke|envia|enviar|manda|mandar|envoie|envoyez|envoyer"
    r"|donne|donnez|partage|partagez|comparte|teile"
    # Added for the fix, from PT-8 to PT-19 only. Not "ask for" or "enter": runbooks say
    # "macOS asks for your password" and "enter your password".
    r"|replying with|respond with|responding with|include"
    r"|ask (?:the |each |every |any )?(?:employee|employees|user|users|person|reader|readers"
    r"|caller|colleague|colleagues|staff|them)"
    r"|demandez|demander|demande|pide|pidele|pida|solicita|frage|fragen|verlange|verlangen)"
)
_PRIVATE_WORDS = (
    r"(password|passwords|passcode|passcodes|sign in details|login details|log in details"
    r"|credentials|mfa code|mfa codes|verification code|one time code|security code|recovery code"
    r"|recovery codes|2fa code|backup code|backup codes|pin|passwort|kennwort|zugangsdaten"
    r"|contrasena|credenciales|mot de passe|identifiants"
    r"|reset code|reset codes|reset link|reset links|one time password|code mfa|codes mfa"
    r"|codigo mfa|codigo de verificacion|code de verification|bestatigungscode)"
)
# A password manager or vault is a tool, not a secret; "never send a password" is a warning.
_CREDENTIALS = re.compile(
    rf"\b{_SEND}\b(?P<between>.{{0,50}}?)\b{_PRIVATE_WORDS}\b(?! manager| vault)"
)
_WARNING_BEFORE = re.compile(r"\b(never|not|dont|don t|nie|niemals|nunca|jamais|ne)\b")

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_LINK = re.compile(r"\b(?:https?://|www\.)([^/\s\"'`)>]+)", re.IGNORECASE)
# A browser ends a link's host at "?", "#" or "\" too, so "https://a.example?.meridian.example"
# opens a.example. Both readings are checked; either one outside meridian.example flags the text.
_LINK_HOST = re.compile(r"\b(?:https?://|www\.)([^/?#\\\s\"'`)>]+)", re.IGNORECASE)

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


def _flag_folded(folded: str) -> str | None:
    """Why one piece of text, already folded, must not be posted, or None."""
    if any(p.search(folded) for p in _COMPILED_ADDRESS):
        return ADDRESSED_TO_BOT
    normalised = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    for kind, pattern in _COMPILED:
        if pattern.search(normalised):
            return kind
    for match in _CREDENTIALS.finditer(normalised):
        before = normalised[max(0, match.start() - 16) : match.start()]
        if not _WARNING_BEFORE.search(before) and not _WARNING_BEFORE.search(
            match.group("between")
        ):
            return CREDENTIALS
    return None


def flag_text(text: str) -> str | None:
    """Why one piece of guide text must not be posted, or None.

    The text is read as before (_fold), then in the look-alike forms (lookalikes.py); the first
    reading that is flagged gives the kind, so a text flagged before keeps its kind."""
    hosts = (*_EMAIL.findall(text), *_LINK.findall(text), *_LINK_HOST.findall(text))
    if not all(_ours(host) for host in hosts):
        return OUTSIDE_ADDRESS
    for folded in dict.fromkeys((_fold(text), *lookalikes.forms(text))):
        kind = _flag_folded(folded)
        if kind is not None:
            return kind
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
