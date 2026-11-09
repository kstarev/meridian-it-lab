"""Does a message ask about someone it describes by role? A code-only signal (ADR-024).

A person word (new starter, intern, colleague, teammate, manager, contractor, assistant, or
a German, Spanish or French equivalent) within a few words of a login word (login,
username, account, user ID, sign-in, or an equivalent) hands the request to a person.

Like the mention check (ADR-017), it never refuses on its own, it is never offered to the
model as a tool, and it reads only the raw message: no model output, no lookups, no I/O.
It catches requests that name nobody the mention check could look up, such as
"what's my new starter's login?" (ADR-023). A person word that only describes the sender
("I'm the new intern") doesn't count.
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


# A person word right after a self-introduction describes the sender ("I'm the new intern",
# "ich bin die neue Praktikantin", "soy el nuevo becario", "je suis le nouveau stagiaire"),
# so that occurrence doesn't fire. Any other person word in the message still does.
SELF_INTROS: tuple[tuple[str, ...], ...] = (
    ("i", "m"),
    ("i", "am"),
    ("ich", "bin"),
    ("soy",),
    ("je", "suis"),
)
# At most this many words between the introduction and the person word ("the new").
MAX_SELF_FILLER = 3
# A possessive in between makes it someone else's role: "I'm their manager", "I'm Alex
# Kim's assistant", "ich bin seine Assistentin", "soy su asistente", "je suis son assistant".
OTHER_OWNER_WORDS = frozenset(
    {
        # English ("s" is the possessive 's)
        "s", "their", "his", "her", "your", "our", "my",
        # German
        "sein", "seine", "seinen", "seinem", "seiner", "seines",
        "ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres",
        "dein", "deine", "deinen", "deinem", "deiner",
        "mein", "meine", "meinen", "meinem", "meiner", "meines",
        "unser", "unsere", "unseren", "unserem", "unserer", "euer", "eure",
        # Spanish
        "su", "sus", "tu", "tus", "mi", "mis",
        "nuestro", "nuestra", "nuestros", "nuestras", "vuestro", "vuestra",
        # French
        "son", "sa", "ses", "leur", "leurs", "ton", "ta", "tes",
        "mon", "ma", "mes", "notre", "nos", "votre", "vos",
    }
)  # fmt: skip


def _is_self_description(tokens: list[str], first: int) -> bool:
    """Whether the person word starting at `first` follows a self-introduction."""
    for intro in SELF_INTROS:
        for filler in range(MAX_SELF_FILLER + 1):
            start = first - filler - len(intro)
            if start < 0 or tuple(tokens[start : start + len(intro)]) != intro:
                continue
            if not OTHER_OWNER_WORDS.intersection(tokens[start + len(intro) : first]):
                return True
    return False


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
    """True when a person word sits within MAX_GAP words of a login word, not counting a
    person word that only describes the sender."""
    tokens = _tokens(text)
    people = [
        span for span in _spans(tokens, PERSON_WORDS) if not _is_self_description(tokens, span[0])
    ]
    if not people:
        return False
    logins = _spans(tokens, LOGIN_WORDS)
    for p_first, p_last in people:
        for l_first, l_last in logins:
            gap = l_first - p_last - 1 if l_first > p_last else p_first - l_last - 1
            if 0 <= gap <= MAX_GAP:
                return True
    return False
