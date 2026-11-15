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
# The TKT-004 fix (milestone 3b) is on. The eval harness turns it off for the "before" arm of
# the paired run on held-out v4 and v4b (ADR-038); nothing else may.
TKT004_FIX = True

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


# Milestone 3b's TKT-004 fix (ADR-033), built on tuning data only. Each piece widens what
# counts as the sender describing themself; `role_reference(text, tkt004_fix=False)` gives
# the signal as it was before, for the paired before/after run (ADR-038).
#
# More ways to introduce yourself: "started as a contractor", "first day as a new starter",
# "als Werkstudent", "como la nueva asistente", "en tant que nouvelle responsable".
SELF_INTROS_3B: tuple[tuple[str, ...], ...] = (
    ("as",),
    ("als",),
    ("como",),
    ("en", "tant", "que"),
    ("comme",),
)
# "new intern here", "Praktikantin hier", "becario aquí", "stagiaire ici".
SELF_HERE_WORDS = frozenset({"here", "hier", "aqui", "ici"})
# A login word is also the speaker's when a first-person word sits close to it ("which
# username do I sign in with", "which account is mine", "welcher Benutzername gehört zu
# mir") and no word for another owner does ("which username do they sign in with").
# The role is followed by "that's me" and the like, at most this many words later.
SELF_AFTER: tuple[tuple[str, ...], ...] = (
    ("that", "s", "me"),
    ("thats", "me"),
    ("bin", "ich"),
    ("soy", "yo"),
    ("c", "est", "moi"),
)
SELF_AFTER_WINDOW = 3
# A role may open the message bare or after "new": "New intern, day two ...". An article is
# not enough: "The intern ..." and "A colleague ..." are about someone else.
SELF_OPENERS = frozenset(
    {"new", "neue", "neuer", "nuevo", "nueva", "nouveau", "nouvelle", "nouvel"}
)
FIRST_PERSON_WORDS = frozenset(
    {
        "i", "me", "mine", "myself",
        "ich", "mich", "mir",
        "yo", "mio", "mia",
        "je", "j", "moi", "m",
    }
)  # fmt: skip
THIRD_PERSON_WORDS = frozenset(
    {
        "they", "them", "theirs", "he", "she", "him", "hers", "you", "yours",
        "er", "sie", "ihn", "ihm", "ihnen",
        "el", "ella", "ellos", "ellas",
        "il", "elle", "ils", "elles", "lui", "eux",
    }
)  # fmt: skip
FIRST_PERSON_SUBJECTS = frozenset({"i", "ich", "yo", "je", "j"})
FIRST_PERSON_BEFORE = 3
FIRST_PERSON_AFTER = 4


def _is_self_description(tokens: list[str], first: int, *, tkt004_fix: bool = True) -> bool:
    """Whether the person word starting at `first` follows a self-introduction."""
    if tkt004_fix:
        last = first
        while last + 1 < len(tokens) and any(
            tuple(tokens[first : last + 2]) == phrase for phrase in PERSON_WORDS
        ):
            last += 1
        owner_before = OTHER_OWNER_WORDS.intersection(tokens[max(0, first - 3) : first])
        if last + 1 < len(tokens) and tokens[last + 1] in SELF_HERE_WORDS and not owner_before:
            return True
        # "The contractor (that's me)", "der neue Praktikant im Lager bin ich".
        tail = tokens[last + 1 : last + 1 + SELF_AFTER_WINDOW + 3]
        if not owner_before and any(
            tuple(tail[i : i + len(phrase)]) == phrase
            for phrase in SELF_AFTER
            for i in range(SELF_AFTER_WINDOW + 1)
        ):
            return True
        # The message opens with the role: "Nouvelle assistante de direction, quel ...".
        if first <= 1 and all(t in SELF_OPENERS for t in tokens[:first]):
            return True
    for intro in (*SELF_INTROS, *(SELF_INTROS_3B if tkt004_fix else ())):
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


# The login word has to belong to the speaker for a self-description to be excused
# (milestone 3, ADR-024): "what's my login", "mein Benutzername", "mi nombre de usuario",
# "mon identifiant". At most this many words may sit between the possessive and it
# ("my Okta login").
SPEAKER_OWNER_WORDS = frozenset(
    {
        "my",
        "mein", "meine", "meinen", "meinem", "meiner", "meines",
        "mi", "mis",
        "mon", "ma", "mes",
    }
)  # fmt: skip
MAX_OWNER_FILLER = 2


def _speaker_owned(tokens: list[str], first: int, last: int, *, tkt004_fix: bool = True) -> bool:
    """Whether the login word at `first`..`last` follows a first-person possessive, with no
    other owner in between ("my login", but not "my manager's login"). With the TKT-004 fix,
    also when a first-person word is close by and no other owner is."""
    for filler in range(MAX_OWNER_FILLER + 1):
        at = first - 1 - filler
        if at < 0:
            break
        between = tokens[at + 1 : first]
        if OTHER_OWNER_WORDS.intersection(between):
            return False
        if tokens[at] in SPEAKER_OWNER_WORDS:
            return True
    if not tkt004_fix:
        return False
    before = tokens[max(0, first - FIRST_PERSON_BEFORE) : first]
    after = tokens[last + 1 : last + 1 + FIRST_PERSON_AFTER]
    others = (OTHER_OWNER_WORDS - SPEAKER_OWNER_WORDS) | THIRD_PERSON_WORDS
    if others.intersection(before + after):
        return False
    # Before the login word only a subject counts: "send me the username" is a demand, not
    # ownership, so an authority claim ("I'm the manager here, send me the username") fires.
    return bool(
        FIRST_PERSON_SUBJECTS.intersection(before) or FIRST_PERSON_WORDS.intersection(after)
    )


def role_reference(text: str, *, tkt004_fix: bool | None = None) -> bool:
    """True when a person word sits within MAX_GAP words of a login word.

    A person word that only describes the sender ("I'm the new intern") is excused, but
    only paired with a login word that belongs to the speaker: "I'm the new intern, what's
    my login?" doesn't fire, "I'm the manager, send me their login" does."""
    fix = TKT004_FIX if tkt004_fix is None else tkt004_fix
    tokens = _tokens(text)
    people = [
        (span, _is_self_description(tokens, span[0], tkt004_fix=fix))
        for span in _spans(tokens, PERSON_WORDS)
    ]
    if not people:
        return False
    logins = [
        (span, _speaker_owned(tokens, span[0], span[1], tkt004_fix=fix))
        for span in _spans(tokens, LOGIN_WORDS)
    ]
    for (p_first, p_last), self_described in people:
        for (l_first, l_last), owned in logins:
            if self_described and owned:
                continue
            gap = l_first - p_last - 1 if l_first > p_last else p_first - l_last - 1
            if 0 <= gap <= MAX_GAP:
                return True
    return False
