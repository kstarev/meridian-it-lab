"""Does a message ask about someone it describes by role? A code-only signal (ADR-024).

A person word (new starter, intern, colleague, teammate, manager, contractor, assistant, or
a German, Spanish or French equivalent) within a few words of a login word (login,
username, account, user ID, sign-in, or an equivalent) hands the request to a person.

Like the mention check (ADR-017), it never refuses on its own, it is never offered to the
model as a tool, and it reads only the raw message: no model output, no lookups, no I/O.
It catches requests that name nobody the mention check could look up, such as
"what's my new starter's login?" (ADR-023).
"""

import re
import unicodedata

# How many words may sit between a person word and a login word ("my new starter's
# login" has one: the "s" of "starter's").
MAX_GAP = 5

# Each entry is a word sequence after normalising (lower case, no accents, letters only).
PERSON_WORDS: tuple[tuple[str, ...], ...] = tuple(
    tuple(phrase.split())
    for phrase in (
        # English
        "new starter",
        "new starters",
        "intern",
        "interns",
        "colleague",
        "colleagues",
        "teammate",
        "teammates",
        "team mate",
        "team mates",
        "manager",
        "managers",
        "contractor",
        "contractors",
        "assistant",
        "assistants",
        # German
        "neuer mitarbeiter",
        "neue mitarbeiterin",
        "neuen mitarbeiter",
        "neuen mitarbeiters",
        "neue mitarbeiter",
        "praktikant",
        "praktikantin",
        "praktikanten",
        "praktikantinnen",
        "kollege",
        "kollegin",
        "kollegen",
        "kolleginnen",
        "teamkollege",
        "teamkollegin",
        "teamkollegen",
        "managerin",
        "vorgesetzter",
        "vorgesetzte",
        "vorgesetzten",
        "chef",
        "chefin",
        "auftragnehmer",
        "auftragnehmerin",
        "assistent",
        "assistentin",
        "assistenten",
        # Spanish
        "nuevo empleado",
        "nueva empleada",
        "nuevos empleados",
        "becario",
        "becaria",
        "becarios",
        "pasante",
        "pasantes",
        "colega",
        "colegas",
        "companero",
        "companera",
        "companeros",
        "companeras",
        "jefe",
        "jefa",
        "gerente",
        "contratista",
        "contratistas",
        "asistente",
        "asistentes",
        # French
        "nouvel employe",
        "nouvelle employee",
        "nouveau collaborateur",
        "nouvelle collaboratrice",
        "nouvelle recrue",
        "nouvel arrivant",
        "nouvelle arrivante",
        "stagiaire",
        "stagiaires",
        "collegue",
        "collegues",
        "coequipier",
        "coequipiere",
        "responsable",
        "sous traitant",
        "prestataire",
        "assistante",
    )
)

LOGIN_WORDS: tuple[tuple[str, ...], ...] = tuple(
    tuple(phrase.split())
    for phrase in (
        # English
        "login",
        "logins",
        "log in",
        "logon",
        "username",
        "usernames",
        "user name",
        "account",
        "accounts",
        "user id",
        "userid",
        "sign in",
        "signin",
        # German
        "anmeldung",
        "anmeldename",
        "anmeldedaten",
        "benutzername",
        "benutzernamen",
        "benutzerkonto",
        "konto",
        "benutzer id",
        "benutzerkennung",
        "zugangsdaten",
        # Spanish
        "usuario",
        "nombre de usuario",
        "cuenta",
        "cuentas",
        "inicio de sesion",
        "id de usuario",
        # French
        "identifiant",
        "identifiants",
        "nom d utilisateur",
        "compte",
        "comptes",
        "connexion",
    )
)


def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", plain)


def _spans(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> list[tuple[int, int]]:
    """(first, last) token index of every phrase occurrence."""
    found = []
    for i in range(len(tokens)):
        for phrase in phrases:
            if tuple(tokens[i : i + len(phrase)]) == phrase:
                found.append((i, i + len(phrase) - 1))
    return found


def role_reference(text: str) -> bool:
    """True when a person word sits within MAX_GAP words of a login word."""
    tokens = _tokens(text)
    people = _spans(tokens, PERSON_WORDS)
    if not people:
        return False
    logins = _spans(tokens, LOGIN_WORDS)
    for p_first, p_last in people:
        for l_first, l_last in logins:
            gap = l_first - p_last - 1 if l_first > p_last else p_first - l_last - 1
            if 0 <= gap <= MAX_GAP:
                return True
    return False
