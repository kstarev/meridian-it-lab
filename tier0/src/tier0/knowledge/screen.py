"""A code-only signal for requests that must never be answered from a guide (ADR-035).

It is a second, independent line behind the model, like the role signal in the username flow
(ADR-023): when it fires, the message goes to a person even if the model picked a guide. It
can only cause a hand-off, never an answer.

It looks for an account action (a reset, an unlock, access, rights, a licence), another
person's account or things, or an incident (a lost or stolen device, a clicked phishing link).
The word lists were written from tuning data only, in English, German, Spanish and French.
It is deliberately narrow: "password" alone doesn't fire, because one guide is about the
password manager. What it misses on new wording, the sealed how-to test will show.

Its patterns stay frozen (ADR-040; a test pins them). Property tests (2026-11-18) found that
mathematical bold capitals and a zero-width character inside a word got past it with no new
wording. It now also reads each message in the forms lookalikes.py makes, which can only make
it fire more, never less.
"""

import re
import unicodedata

from tier0.knowledge import lookalikes

ACCOUNT_ACTION = "account_action"
SOMEONE_ELSE = "someone_else"
INCIDENT = "incident"

_SIGN_IN_WORDS = (
    r"(password|passwort|kennwort|contrasena|mot de passe|mfa|2fa|two factor|authenticator"
    r"|okta verify|double authentification|zwei faktor)"
)
_RESET_BEFORE = r"\b(reset|zurucksetzen|restablecer|reinitialiser|re enrol|re enroll)\b"
_RESET_AFTER = (
    r"\b(reset|zurucksetzen|zuruckgesetzt|restablecer|reinitialiser|re enrol|re enroll"
    r"|vergessen|olvide|oublie)\b"
)
_DEVICE = r"\b(laptop|phone|handy|ordinateur|portable|telefono|portatil|badge|macbook)\b"
_PATTERNS: tuple[tuple[str, str], ...] = (
    # Resets, unlocks and re-enrolment.
    (ACCOUNT_ACTION, rf"{_RESET_BEFORE}.{{0,40}}{_SIGN_IN_WORDS}"),
    (ACCOUNT_ACTION, rf"{_SIGN_IN_WORDS}.{{0,40}}{_RESET_AFTER}"),
    (ACCOUNT_ACTION, r"\b(forgot|forgotten)\b.{0,20}\b(password|passcode)\b"),
    (
        ACCOUNT_ACTION,
        r"\b(locked out|unlock my|unlock the account|entsperren|desbloquear|debloquer)\b",
    ),
    # Access, rights, licences.
    (ACCOUNT_ACTION, r"\b(get|need|give me|grant|have) (me )?(access|edit rights|admin rights)\b"),
    (ACCOUNT_ACTION, r"\b(access|zugriff|acceso|acces) (to|auf|a|au|aux|a la|al)\b"),
    (
        ACCOUNT_ACTION,
        r"\b(admin rights|adminrechte|derechos de administrador|droits admin\w*)\b.{0,30}"
        r"\b(need|give|brauche|necesito|besoin)\b",
    ),
    (
        ACCOUNT_ACTION,
        r"\b(need|give|brauche|necesito|besoin)\b.{0,30}"
        r"\b(admin rights|adminrechte|a licen[cs]e|a seat|lizenz|licencia)\b",
    ),
    (ACCOUNT_ACTION, r"\badd me to\b"),
    # Incidents.
    (
        INCIDENT,
        r"\b(lost|stolen|verloren|gestohlen|perdu|vole|perdi|robaron|robado)\b.{0,40}" + _DEVICE,
    ),
    (
        INCIDENT,
        r"\b(laptop|phone|handy|ordinateur|telefono|portatil|macbook)\b.{0,40}"
        r"\b(lost|stolen|verloren|gestohlen|perdu|vole|robado)\b",
    ),
    (INCIDENT, r"\bclicked\b.{0,40}\b(link|attachment)\b"),
    (INCIDENT, r"\b(ransom|files are encrypted|signed in to my account from)\b"),
)
_COMPILED = tuple((kind, re.compile(pattern)) for kind, pattern in _PATTERNS)


def _normalise(text: str) -> str:
    """Lower case, no accents, letters and digits only, single spaces."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def hand_off_signal(text: str) -> str | None:
    """Why this message must go to a person, or None when the signal doesn't fire.

    The message is read as before (_normalise), then in the look-alike forms (lookalikes.py);
    the first reading that fires gives the kind, so a message that fired before keeps its kind."""
    readings = (_normalise(text), *(_words(form) for form in lookalikes.forms(text)))
    for normalised in dict.fromkeys(readings):
        for kind, pattern in _COMPILED:
            if pattern.search(normalised):
                return kind
    return None


def _words(folded: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()
