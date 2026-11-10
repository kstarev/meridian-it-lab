"""Pre-approved text for proactive posts. Code only, never a model (ADR-020).

English, German, Spanish and French have reviewed templates. Portuguese, Polish, Dutch
and Japanese offices get English until a native speaker has reviewed text for them; the
scheduler refuses to enable a language that has no reviewed templates here, so no
unreviewed text can reach a company-wide post.

Wording is inclusive ("happy holidays") and names the country, never the holiday.
Variants rotate by a hash of the post id, so a schedule repeats exactly.
"""

import hashlib
from collections.abc import Sequence
from datetime import date

ON_CALL = "#it-oncall"
RECOVERY_URL = "https://help.meridian.example/verify-identity"
REVIEWED_LANGUAGES = frozenset({"en", "de", "es", "fr"})
ALLOWED_EMOJI = ("🎉", "🌴", "🙂", "👋")

COUNTRY_NAMES: dict[str, dict[str, str]] = {
    "en": {
        "US": "the US",
        "CA": "Canada",
        "BR": "Brazil",
        "MX": "Mexico",
        "DE": "Germany",
        "GB": "the UK",
        "PL": "Poland",
        "NL": "the Netherlands",
        "ES": "Spain",
        "IE": "Ireland",
        "SG": "Singapore",
        "AU": "Australia",
        "JP": "Japan",
        "IN": "India",
    },
    # Dative, for "in ...".
    "de": {
        "US": "den USA",
        "CA": "Kanada",
        "BR": "Brasilien",
        "MX": "Mexiko",
        "DE": "Deutschland",
        "GB": "Großbritannien",
        "PL": "Polen",
        "NL": "den Niederlanden",
        "ES": "Spanien",
        "IE": "Irland",
        "SG": "Singapur",
        "AU": "Australien",
        "JP": "Japan",
        "IN": "Indien",
    },
    "es": {
        "US": "Estados Unidos",
        "CA": "Canadá",
        "BR": "Brasil",
        "MX": "México",
        "DE": "Alemania",
        "GB": "el Reino Unido",
        "PL": "Polonia",
        "NL": "los Países Bajos",
        "ES": "España",
        "IE": "Irlanda",
        "SG": "Singapur",
        "AU": "Australia",
        "JP": "Japón",
        "IN": "India",
    },
    # Used after a colon, so no article or preposition is needed.
    "fr": {
        "US": "États-Unis",
        "CA": "Canada",
        "BR": "Brésil",
        "MX": "Mexique",
        "DE": "Allemagne",
        "GB": "Royaume-Uni",
        "PL": "Pologne",
        "NL": "Pays-Bas",
        "ES": "Espagne",
        "IE": "Irlande",
        "SG": "Singapour",
        "AU": "Australie",
        "JP": "Japon",
        "IN": "Inde",
    },
}
# Plain (nominative) names for lists like "Freie Tage: USA (...)"; only German differs.
_PLAIN_DE = {"US": "USA", "NL": "Niederlande", "GB": "Großbritannien"}
_AND = {"en": "and", "de": "und", "es": "y", "fr": "et"}


def plain_name(code: str, language: str) -> str:
    if language == "de":
        return _PLAIN_DE.get(code, COUNTRY_NAMES["de"][code])
    return COUNTRY_NAMES[language][code]


_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
}
_MONTHS = {
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
    "de": (
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ),
    "es": (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ),
    "fr": (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ),
}  # fmt: skip


def variant(key: str, count: int) -> int:
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


def join(items: Sequence[str], language: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {_AND[language]} {items[-1]}"


def format_day(day: date, language: str) -> str:
    wd, month = _WEEKDAYS[language][day.weekday()], _MONTHS[language][day.month - 1]
    if language == "de":
        return f"{wd}, {day.day}. {month}"
    if language == "es":
        return f"{wd} {day.day} de {month}"
    if language == "fr":
        return f"{wd} {'1er' if day.day == 1 else day.day} {month}"
    return f"{wd} {day.day} {month}"


def countries(codes: Sequence[str], language: str) -> str:
    return join([COUNTRY_NAMES[language][c] for c in codes], language)


HEADLINES: dict[str, tuple[str, ...]] = {
    "en": (
        "Happy holidays to everyone in {countries}! 🎉",
        "A few days off are coming up for our colleagues in {countries}. Happy holidays! 🌴",
        "Time for a break in {countries}. Happy holidays! 🙂",
    ),
    "de": (
        "Frohe Feiertage an alle in {countries}! 🎉",
        "Für unsere Kolleginnen und Kollegen in {countries} stehen freie Tage an. "
        "Frohe Feiertage! 🌴",
        "Zeit für eine Pause in {countries}. Frohe Feiertage! 🙂",
    ),
    "es": (
        "¡Felices fiestas a todo el equipo en {countries}! 🎉",
        "Se acercan unos días libres para nuestros compañeros en {countries}. ¡Felices fiestas! 🌴",
        "Llega un descanso en {countries}. ¡Felices fiestas! 🙂",
    ),
    "fr": (
        "Bonnes fêtes à toute l'équipe : {countries} ! 🎉",
        "Quelques jours de repos arrivent pour nos collègues : {countries}. Bonnes fêtes ! 🌴",
        "C'est l'heure d'une pause : {countries}. Bonnes fêtes ! 🙂",
    ),
}
DAYS_OFF: dict[str, str] = {
    "en": "Days off: {days}.",
    "de": "Freie Tage: {days}.",
    "es": "Días libres: {days}.",
    "fr": "Jours de repos : {days}.",
}
COVERAGE: dict[str, dict[str, str]] = {
    "it_open": {
        "en": "IT is working as usual, and I can still help with usernames any time.",
        "de": "Die IT arbeitet wie gewohnt, und bei Benutzernamen helfe ich jederzeit.",
        "es": "TI trabaja con normalidad, y te ayudo con los nombres de usuario cuando quieras.",
        "fr": "L'équipe informatique travaille normalement, et je reste là pour les identifiants.",
    },
    "it_off": {
        "en": "IT is off on {it_days} too. I can still help with usernames, and urgent "
        "issues go to " + ON_CALL + ".",
        "de": "Die IT ist am {it_days} ebenfalls frei. Bei Benutzernamen helfe ich trotzdem, "
        "und Dringendes geht an " + ON_CALL + ".",
        "es": "TI también descansa el {it_days}. Sigo ayudando con los nombres de usuario, y "
        "lo urgente va a " + ON_CALL + ".",
        "fr": "L'équipe informatique est aussi en repos le {it_days}. Je reste là pour les "
        "identifiants, et l'urgent va sur " + ON_CALL + ".",
    },
    "weekend": {
        "en": "It falls on a weekend, so IT has its usual weekend cover: I can still help "
        "with usernames, and urgent issues go to " + ON_CALL + ".",
        "de": "Es ist ein Wochenende, also gilt die übliche Wochenendbereitschaft der IT: "
        "Bei Benutzernamen helfe ich, Dringendes geht an " + ON_CALL + ".",
        "es": "Cae en fin de semana, así que TI tiene su guardia habitual: te ayudo con los "
        "nombres de usuario, y lo urgente va a " + ON_CALL + ".",
        "fr": "Ça tombe un week-end, donc l'informatique a sa permanence habituelle : je "
        "reste là pour les identifiants, et l'urgent va sur " + ON_CALL + ".",
    },
}
YEAR_END: dict[str, tuple[str, ...]] = {
    "en": (
        "The year-end break is almost here. Happy holidays, everyone, however you celebrate! 🎉",
        "Thanks for a great year, all. Enjoy the break, and happy holidays! 🌴",
        "Happy holidays to all of you, in every office. See you in the new year! 👋",
    ),
    "de": (
        "Die Pause zum Jahresende ist fast da. Frohe Feiertage an alle, wie auch immer "
        "ihr feiert! 🎉",
        "Danke für ein tolles Jahr. Genießt die Pause und frohe Feiertage! 🌴",
        "Frohe Feiertage an euch alle, in jedem Büro. Bis im neuen Jahr! 👋",
    ),
    "es": (
        "Ya casi llega el descanso de fin de año. ¡Felices fiestas a todos, lo celebréis "
        "como lo celebréis! 🎉",
        "Gracias por un gran año. ¡Disfrutad del descanso y felices fiestas! 🌴",
        "¡Felices fiestas a todos, en cada oficina! Nos vemos en el año nuevo. 👋",
    ),
    "fr": (
        "La pause de fin d'année arrive. Bonnes fêtes à toutes et à tous, quelle que soit "
        "votre façon de fêter ! 🎉",
        "Merci pour cette belle année. Profitez de la pause, et bonnes fêtes ! 🌴",
        "Bonnes fêtes à vous tous, dans chaque bureau. À l'année prochaine ! 👋",
    ),
}
YEAR_END_COVERAGE: dict[str, str] = {
    "en": "IT runs reduced cover from {start} to {end}. I can still help with usernames, "
    "and urgent issues go to " + ON_CALL + ".",
    "de": "Die IT ist vom {start} bis {end} nur eingeschränkt erreichbar. Bei "
    "Benutzernamen helfe ich, Dringendes geht an " + ON_CALL + ".",
    "es": "TI funciona con servicio reducido del {start} al {end}. Sigo ayudando con los "
    "nombres de usuario, y lo urgente va a " + ON_CALL + ".",
    "fr": "L'informatique tourne en service réduit du {start} au {end}. Je reste là pour "
    "les identifiants, et l'urgent va sur " + ON_CALL + ".",
}
PULSE: dict[str, tuple[str, ...]] = {
    "en": (
        "It's been quiet in here for a few days 🙂 Anything IT-related on your mind? Ask "
        "away, nothing's too small.",
        "Just checking in: #it-help has been quiet. If something's been bugging you, now's "
        "a good time to mention it.",
        "Quiet days in #it-help! If your laptop, login or anything else is acting up, drop "
        "a message here.",
    ),
    "de": (
        "Hier war es ein paar Tage ruhig 🙂 Gibt es etwas rund um die IT? Frag ruhig, "
        "nichts ist zu klein.",
        "Kurz nachgefragt: In #it-help war es still. Wenn dich etwas nervt, ist jetzt ein "
        "guter Moment dafür.",
        "Ruhige Tage in #it-help! Wenn Laptop, Login oder sonst etwas zickt, schreib es "
        "einfach hier.",
    ),
    "es": (
        "Esto ha estado tranquilo unos días 🙂 ¿Tienes algo de TI en mente? Pregunta, nada "
        "es demasiado pequeño.",
        "Solo para saber cómo vais: #it-help ha estado tranquilo. Si algo te molesta, es un "
        "buen momento para contarlo.",
        "¡Días tranquilos en #it-help! Si el portátil, el acceso u otra cosa falla, escribe aquí.",
    ),
    "fr": (
        "C'est calme ici depuis quelques jours 🙂 Une question informatique ? Demande, rien "
        "n'est trop petit.",
        "Petit point : #it-help est resté calme. Si quelque chose t'embête, c'est le bon "
        "moment pour en parler.",
        "Journées calmes sur #it-help ! Si ton portable, ta connexion ou autre chose fait "
        "des siennes, écris ici.",
    ),
}
TIPS: dict[str, tuple[str, ...]] = {
    "en": (
        'Tip: I can tell you your username any time. Just DM me "what\'s my username?"',
        "Tip: locked out? Start at " + RECOVERY_URL + " to verify your identity.",
        "Tip: urgent issues outside office hours go to " + ON_CALL + ".",
    ),
    "de": (
        "Tipp: Deinen Benutzernamen sage ich dir jederzeit. Schreib mir einfach „Wie lautet "
        "mein Benutzername?“",
        "Tipp: Ausgesperrt? Unter " + RECOVERY_URL + " bestätigst du deine Identität.",
        "Tipp: Dringendes außerhalb der Bürozeiten geht an " + ON_CALL + ".",
    ),
    "es": (
        "Consejo: te digo tu nombre de usuario cuando quieras. Escríbeme «¿Cuál es mi "
        "nombre de usuario?»",
        "Consejo: ¿sin acceso? Empieza en " + RECOVERY_URL + " para verificar tu identidad.",
        "Consejo: lo urgente fuera de horario va a " + ON_CALL + ".",
    ),
    "fr": (
        "Astuce : je te donne ton identifiant à tout moment. Écris-moi « Quel est mon "
        "identifiant ? »",
        "Astuce : bloqué ? Commence par " + RECOVERY_URL + " pour vérifier ton identité.",
        "Astuce : l'urgent en dehors des heures de bureau va sur " + ON_CALL + ".",
    ),
}
PREVIEW = (
    "Preview: this post is scheduled for {when} in {channel}. If it shouldn't go out, press Cancel."
)
PULSE_PREVIEW = (
    "Preview: if #it-help stays quiet, I'll post this in #it-help on {when}. If it "
    "shouldn't go out, press Cancel."
)
BLOCKED_ALERT = (
    "I didn't send the proactive post {post_id}: the output guard blocked it. Please check "
    "the templates."
)


def holiday_section(
    language: str,
    key: str,
    items: Sequence[tuple[str, tuple[date, ...], str, tuple[date, ...]]],
) -> str:
    """One language's part of a holiday note. Items: (country, holiday dates, coverage
    kind, IT-off dates). Countries with the same days are listed together."""
    codes = [c for c, *_ in items]
    lines = [
        HEADLINES[language][variant(f"{key}:head", 3)].format(countries=countries(codes, language))
    ]
    same = len({days for _, days, _, _ in items}) == 1
    if same:
        lines.append(
            DAYS_OFF[language].format(
                days=join([format_day(d, language) for d in items[0][1]], language)
            )
        )
    else:
        parts = [
            f"{plain_name(c, language)} ({join([format_day(d, language) for d in days], language)})"
            for c, days, _, _ in items
        ]
        lines.append(DAYS_OFF[language].format(days="; ".join(parts)))
    kinds = {kind for _, _, kind, _ in items}
    kind = "it_off" if "it_off" in kinds else ("it_open" if "it_open" in kinds else "weekend")
    it_days = sorted({d for *_, off in items for d in off})
    coverage = COVERAGE[kind][language]
    if kind == "it_off":
        coverage = coverage.format(
            it_days=join([format_day(d, language) for d in it_days], language)
        )
    lines.append(coverage)
    return "\n".join(lines)


def year_end_section(language: str, key: str, start: date, end: date) -> str:
    head = YEAR_END[language][variant(f"{key}:yearend", 3)]
    cover = YEAR_END_COVERAGE[language].format(
        start=format_day(start, language), end=format_day(end, language)
    )
    return f"{head}\n{cover}"


def pulse_text(language: str, key: str) -> str:
    return (
        f"{PULSE[language][variant(f'{key}:pulse', 3)]}\n{TIPS[language][variant(f'{key}:tip', 3)]}"
    )
