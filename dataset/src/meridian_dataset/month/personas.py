"""The people who send the month's tickets: 230 fake Meridian staff in 14 countries.

The roster comes from its own fixed seed, so every month is the same company and a month's
seed only changes the traffic. Each person writes in one language: German in Germany,
Spanish in Spain and Mexico, French in Québec, and English everywhere else (Slack replies in
other locales fall back to English, ADR-028). Usernames follow the fact sheet:
first.last@meridian.example, and a repeated name gets a number. All names are made up.
"""

import random
import unicodedata
from dataclasses import dataclass

DOMAIN = "meridian.example"
PERSONA_SEED = 230
COMPANY_SIZE = 230

# (country, language, Slack locale, UTC offset in November in minutes, people). The 14
# countries of the fact sheet; Canada has an English and a French-speaking group.
ROSTER: tuple[tuple[str, str, str, int, int], ...] = (
    ("US", "en", "en-US", -300, 50),
    ("CA", "en", "en-GB", -300, 18),
    ("GB", "en", "en-GB", 0, 22),
    ("IE", "en", "en-GB", 0, 8),
    ("AU", "en", "en-AU", 660, 12),
    ("SG", "en", "en-GB", 480, 10),
    ("IN", "en", "en-IN", 330, 15),
    ("NL", "en", "en-GB", 60, 8),
    ("PL", "en", "en-GB", 60, 7),
    ("JP", "en", "ja-JP", 540, 5),
    ("BR", "en", "pt-BR", -180, 6),
    ("DE", "de", "de-DE", 60, 23),
    ("ES", "es", "es-ES", 60, 13),
    ("MX", "es", "es-LA", -360, 10),
    ("CA", "fr", "fr-CA", -300, 23),
)
# Departments of everyone outside IT, with exact head counts (227 people).
DEPARTMENT_SIZES: tuple[tuple[str, int], ...] = (
    ("Engineering", 72),
    ("Sales", 46),
    ("Customer Success", 42),
    ("Marketing", 26),
    ("Finance", 22),
    ("People", 19),
)
# The IT team (lead + 2 engineers, Berlin and Toronto) is in the roster but never sends a
# ticket to itself.
IT = "IT"

# fmt: off
GIVEN: dict[str, tuple[str, ...]] = {
    "en": (
        "Olivia", "James", "Amelia", "Daniel", "Grace", "Marcus", "Aaliyah", "Owen",
        "Nadia", "Samuel", "Ruby", "Isaac", "Maya", "Caleb", "Zoe", "Arjun",
        "Ananya", "Wei", "Hiro", "Aiko", "Sienna", "Callum", "Niamh", "Declan",
        "Tariq", "Leah", "Ben", "Freya", "Rohan", "Divya", "Connor", "Imogen",
        "Nathan", "Talia", "Ezra", "Harper", "Felipe", "Bianca", "Jakub", "Marta",
    ),
    "de": (
        "Lena", "Felix", "Johanna", "Maximilian", "Clara", "Moritz", "Greta", "Paul",
        "Mia", "Leon", "Emilia", "Jakob", "Marie", "Tobias", "Katharina", "Florian",
    ),
    "es": (
        "Lucía", "Javier", "Carmen", "Alejandro", "Elena", "Sergio", "Paula", "Andrés",
        "Valentina", "Mateo", "Daniela", "Hugo", "Ximena", "Rodrigo", "Inés", "Santiago",
    ),
    "fr": (
        "Camille", "Étienne", "Chloé", "Mathieu", "Élodie", "Julien", "Gabrielle", "Olivier",
        "Maude", "Simon", "Amélie", "Félix", "Laurence", "Antoine", "Noémie", "Benoît",
    ),
}
FAMILY: dict[str, tuple[str, ...]] = {
    "en": (
        "Bennett", "Hughes", "Patel", "Nguyen", "O'Brien", "Fitzgerald", "Walker", "Turner",
        "Reid", "Campbell", "Foster", "Morgan", "Kaur", "Menon", "Rao", "Tan",
        "Lim", "Wong", "Yamamoto", "Nakamura", "Kowalczyk", "Nowak", "de Vries", "Bakker",
        "Visser", "Oliveira", "Costa", "Ferreira", "Sullivan", "Byrne", "Ellis", "Price",
        "Carter", "Hayes", "Doyle", "Mitchell", "Grant", "Parker", "Shaw", "Russell",
    ),
    "de": (
        "Müller", "Fischer", "Wagner", "Hoffmann", "Schäfer", "Koch", "Richter", "Klein",
        "Wolf", "Neumann", "Schwarz", "Zimmermann", "Braun", "Krüger", "Hartmann", "Lange",
    ),
    "es": (
        "Fernández", "Martínez", "Ruiz", "Moreno", "Jiménez", "Navarro", "Torres", "Romero",
        "Herrera", "Castillo", "Vargas", "Ortega", "Delgado", "Morales", "Ramos", "Gil",
    ),
    "fr": (
        "Tremblay", "Gagnon", "Roy", "Côté", "Bouchard", "Gauthier", "Morin", "Lavoie",
        "Fortin", "Gagné", "Ouellet", "Pelletier", "Bélanger", "Lévesque", "Bergeron", "Leblanc",
    ),
}
# fmt: on

_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    given_name: str
    family_name: str
    email: str
    department: str
    country: str
    language: str
    locale: str
    utc_offset_minutes: int
    slack_user_id: str

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"


def _ascii(name: str) -> str:
    """'Müller' -> 'muller', "O'Brien" -> 'obrien', 'de Vries' -> 'devries'."""
    folded = unicodedata.normalize("NFKD", name)
    return "".join(c for c in folded if c.isascii() and c.isalnum()).lower()


def build_personas(seed: int = PERSONA_SEED) -> tuple[Persona, ...]:
    """The company's 230 people, in a fixed order."""
    rng = random.Random(seed)
    rows: list[tuple[str, str, str, str, int]] = []
    for country, lang, _locale, offset, count in ROSTER:
        for _ in range(count):
            rows.append((rng.choice(GIVEN[lang]), rng.choice(FAMILY[lang]), country, lang, offset))
    # IT sits in Berlin and Toronto: the first German row and the first two English rows in
    # Canada. Everyone else gets a department from the exact head counts.
    first_german = next(i for i, r in enumerate(rows) if r[2] == "DE")
    english_canada = [i for i, r in enumerate(rows) if r[2] == "CA" and r[3] == "en"]
    it_rows = {first_german, *english_canada[:2]}
    departments = [name for name, size in DEPARTMENT_SIZES for _ in range(size)]
    rng.shuffle(departments)
    locales = {(c, lang): loc for c, lang, loc, _, _ in ROSTER}
    seen: dict[str, int] = {}
    taken: set[str] = set()
    people: list[Persona] = []
    others = iter(departments)
    for i, (given, family, country, lang, offset) in enumerate(rows):
        base = f"{_ascii(given)}.{_ascii(family)}"
        seen[base] = seen.get(base, 0) + 1
        suffix = "" if seen[base] == 1 else str(seen[base])
        while True:
            slack = "U" + "".join(rng.choice(_ID_CHARS) for _ in range(10))
            if slack not in taken:
                taken.add(slack)
                break
        people.append(
            Persona(
                id=f"P{i + 1:03d}",
                given_name=given,
                family_name=family,
                email=f"{base}{suffix}@{DOMAIN}",
                department=IT if i in it_rows else next(others),
                country=country,
                language=lang,
                locale=locales[(country, lang)],
                utc_offset_minutes=offset,
                slack_user_id=slack,
            )
        )
    if len(people) != COMPANY_SIZE:
        raise AssertionError("the roster must add up to the company size")
    return tuple(people)
