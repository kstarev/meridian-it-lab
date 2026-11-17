"""Does a message ask for a password or MFA reset? A code-only signal (milestone 3d part 2).

A reset is Tier C: a person does it, after an ID check and a call-back or the manager's
approval (ADR-043). tier0 never does one; it only prepares the ticket. This signal tells the
agent a message is such a request, whatever triage said, so a reset that triage sends down
the username path still goes to a person.

It fires when, within a few words of each other:

- a password or factor word (password, MFA, authenticator, passkey...) sits near a reset
  word (reset, forgot, expired, lost, stolen, new phone, set up again...); or
- an account, password or factor word sits near a lock word (locked, unlock...).

English, German, Spanish and French, written from tuning data only (ADR-013). Like the role
signal (ADR-024), it reads only the raw message: no model output, no lookups, no I/O. It
never refuses and never answers; it can only send a ticket to a person. It is used only by
setups that turn on part 2's Tier C handling, so every earlier setup runs as it did.
"""

import re
import unicodedata

# How many words may sit between the two words of a pair.
MAX_GAP = 6
# "new password", "neues Passwort": "new" counts only right next to a password word.
NEW_GAP = 1


def _phrases(*items: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(item.split()) for item in items)


PASSWORD_WORDS = _phrases(
    "password",
    "passwords",
    "passwort",
    "passworter",
    "kennwort",
    "contrasena",
    "contrasenas",
    "mot de passe",
    "mots de passe",
    "mdp",
)
FACTOR_WORDS = _phrases(
    "mfa",
    "2fa",
    "two factor",
    "multi factor",
    "authenticator",
    "okta verify",
    "passkey",
    "passkeys",
    "security key",
    "yubikey",
    "otp",
    "totp",
    "zwei faktor",
    "zweifaktor",
    "authentifizierung",
    "authentifizierungs",
    "autenticador",
    "autenticacion",
    "doble factor",
    "dos pasos",
    "authentificateur",
    "authentification",
    "double authentification",
    "double facteur",
    "cle de securite",
)
ACCOUNT_WORDS = _phrases(
    "account", "konto", "benutzerkonto", "cuenta", "compte", "login", "acceso", "acces"
)  # fmt: skip
# Word starts: "reset", "resetting"; "zurucksetzen"; "restablecer", "restablezcan";
# "reinitialiser", "reinicializar"; "re-enrol" is "re", "enrol".
RESET_STEMS = (
    "reset",
    "zurucksetz",
    "zuruckgesetz",
    "restablec",
    "restablez",
    "resete",
    "reinicializ",
    "reinitialis",
    "renouvel",
    "reenrol",
    "neu einricht",
)
RESET_WORDS = _phrases(
    # English
    "forgot", "forgotten", "forget", "expired", "expire", "expires", "lost", "lose",
    "stolen", "wiped", "re enrol", "re enroll", "set up again", "temporary",
    # German
    "vergessen", "abgelaufen", "verloren", "gestohlen", "geloscht", "kaputt",
    "neu einrichten", "temporares",
    # Spanish
    "olvide", "olvidado", "olvidada", "olvido", "caducado", "caducada", "caduco",
    "expirado", "expirada", "perdi", "perdido", "perdida", "robaron", "robado", "robo",
    "borrado", "borro", "temporal", "provisional",
    # French
    "oublie", "oubliee", "expire", "perdu", "perdue", "vole", "efface", "provisoire",
    "temporaire",
)  # fmt: skip
# A factor (not a password) also needs a reset after a phone change: "swapped phones".
PHONE_CHANGE_WORDS = _phrases(
    "new phone", "swapped phones", "changed phones", "switched phones", "new device",
    "neues handy", "neues telefon", "handywechsel", "movil nuevo", "nuevo movil",
    "telefono nuevo", "cambiar de movil", "cambie de movil", "nouveau telephone",
    "change de telephone", "nouveau portable",
)  # fmt: skip
NEW_WORDS = _phrases("new", "neues", "neue", "neuen", "nueva", "nuevo", "nouveau", "nouvel")
LOCK_STEMS = ("unlock", "entsperr", "desbloque", "debloqu", "bloquead", "gesperr")
LOCK_WORDS = _phrases("locked", "lockout", "locked out", "sperre", "bloque", "bloquee")


def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", plain)


def _spans(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> list[tuple[int, int]]:
    found = []
    for i in range(len(tokens)):
        for phrase in phrases:
            if tuple(tokens[i : i + len(phrase)]) == phrase:
                found.append((i, i + len(phrase) - 1))
    return found


def _stem_spans(tokens: list[str], stems: tuple[str, ...]) -> list[tuple[int, int]]:
    """Single words that start with a stem, and two-word stems ("neu einricht")."""
    found = []
    for i, token in enumerate(tokens):
        for stem in stems:
            parts = stem.split()
            if len(parts) == 1 and token.startswith(stem):
                found.append((i, i))
            elif (
                len(parts) == 2
                and token == parts[0]
                and i + 1 < len(tokens)
                and tokens[i + 1].startswith(parts[1])
            ):
                found.append((i, i + 1))
    return found


def _separable_reset(tokens: list[str]) -> list[tuple[int, int]]:
    """German splits "zurücksetzen": "setz das MFA zurück". "zuruck" counts as a reset word
    when a form of "setzen" comes at most MAX_GAP words before it."""
    return [
        (i, i)
        for i, word in enumerate(tokens)
        if word == "zuruck"
        and any(t.startswith("setz") for t in tokens[max(0, i - MAX_GAP - 1) : i])
    ]


def _near(a: list[tuple[int, int]], b: list[tuple[int, int]], gap: int = MAX_GAP) -> bool:
    for a_first, a_last in a:
        for b_first, b_last in b:
            apart = b_first - a_last - 1 if b_first > a_last else a_first - b_last - 1
            if apart <= gap:
                return True
    return False


def reset_request(text: str) -> bool:
    """True when the message asks for a password or MFA reset, a new factor or an unlock."""
    tokens = _tokens(text)
    passwords = _spans(tokens, PASSWORD_WORDS)
    factors = _spans(tokens, FACTOR_WORDS)
    accounts = _spans(tokens, ACCOUNT_WORDS)
    resets = [
        *_spans(tokens, RESET_WORDS),
        *_stem_spans(tokens, RESET_STEMS),
        *_separable_reset(tokens),
    ]
    locks = [*_spans(tokens, LOCK_WORDS), *_stem_spans(tokens, LOCK_STEMS)]
    credentials = passwords + factors
    if _near(credentials, resets):
        return True
    if _near(factors, _spans(tokens, PHONE_CHANGE_WORDS)):
        return True
    if _near(passwords, _spans(tokens, NEW_WORDS), gap=NEW_GAP):
        return True
    # A reset verb with no object still asks for one when a factor or password word is near
    # ("reset" and "MFA" above); a lock needs an account, password or factor word near it.
    return _near(accounts + credentials, locks)
