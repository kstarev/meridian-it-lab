"""A code-only signal for requests that must never be answered from a guide (ADR-035).

It is a second, independent line behind the model, like the role signal in the username flow
(ADR-023): when it fires, the message goes to a person even if the model picked a guide. It
can only cause a hand-off, never an answer.

It looks for an account action (a reset, an unlock, access, rights, a licence), another
person's account or things, or an incident (a lost or stolen device, a clicked phishing link).
The word lists were written from tuning data only, in English, German, Spanish and French.
It is deliberately narrow: "password" alone doesn't fire, because one guide is about the
password manager. What it misses on new wording, the sealed how-to test will show.
"""

import re
import unicodedata

ACCOUNT_ACTION = "account_action"
SOMEONE_ELSE = "someone_else"
INCIDENT = "incident"

_SECRET = r"(password|passwort|kennwort|contrasena|mot de passe|mfa|2fa|two factor|authenticator|okta verify|double authentification|zwei faktor)"
_PATTERNS: tuple[tuple[str, str], ...] = (
    # Resets, unlocks and re-enrolment.
    (
        ACCOUNT_ACTION,
        rf"\b(reset|zurucksetzen|restablecer|reinitialiser|re enrol|re enroll)\b.{{0,40}}{_SECRET}",
    ),
    (
        ACCOUNT_ACTION,
        rf"{_SECRET}.{{0,40}}\b(reset|zurucksetzen|zuruckgesetzt|restablecer|reinitialiser|re enrol|re enroll|vergessen|olvide|oublie)\b",
    ),
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
        r"\b(admin rights|adminrechte|derechos de administrador|droits admin\w*)\b.{0,30}\b(need|give|brauche|necesito|besoin)\b",
    ),
    (
        ACCOUNT_ACTION,
        r"\b(need|give|brauche|necesito|besoin)\b.{0,30}\b(admin rights|adminrechte|a licen[cs]e|a seat|lizenz|licencia)\b",
    ),
    (ACCOUNT_ACTION, r"\badd me to\b"),
    # Incidents.
    (
        INCIDENT,
        r"\b(lost|stolen|verloren|gestohlen|perdu|vole|perdi|robaron|robado)\b.{0,40}\b(laptop|phone|handy|ordinateur|portable|telefono|portatil|badge|macbook)\b",
    ),
    (
        INCIDENT,
        r"\b(laptop|phone|handy|ordinateur|telefono|portatil|macbook)\b.{0,40}\b(lost|stolen|verloren|gestohlen|perdu|vole|robado)\b",
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
    """Why this message must go to a person, or None when the signal doesn't fire."""
    normalised = _normalise(text)
    for kind, pattern in _COMPILED:
        if pattern.search(normalised):
            return kind
    return None
