"""What tier0 says. The model classifies; code speaks (ADR-028).

Every message comes from these templates, in the language of the requester's Slack
locale (users.info with include_locale), never from a model. Each reply type has a few
variants, picked by a hash of the ticket so a run can be repeated exactly, never at
random. The first message to a requester says it comes from an AI (EU AI Act Art. 50);
later ones don't repeat it. tier0 only ever calls itself tier0, never a human name.

No user enumeration: what a reply says depends only on the reply type, the requester's
language and first name and the ticket, never on anyone the message mentions. A refusal
or an outsider reply reads the same whether or not the person named exists.
"""

import hashlib
import re
from typing import Literal

type Language = Literal["en", "de", "es", "fr"]
type ReplyType = Literal[
    "answer", "refuse_colleague", "unverified", "handoff", "guard_fallback", "auto_close"
]

LANGUAGES: tuple[Language, ...] = ("en", "de", "es", "fr")
DEFAULT_LANGUAGE: Language = "en"
REPLY_TYPES: tuple[ReplyType, ...] = (
    "answer",
    "refuse_colleague",
    "unverified",
    "handoff",
    "guard_fallback",
    "auto_close",
)
# The facts a reply type must carry; body() refuses to drop one.
REQUIRED_FIELDS: dict[ReplyType, tuple[str, ...]] = {
    "answer": ("username",),
    "refuse_colleague": ("recovery_url",),
    "unverified": ("recovery_url",),
    "handoff": (),
    "guard_fallback": (),
    "auto_close": (),
}

# The first message: a greeting and the AI disclosure.
INTRO: dict[Language, str] = {
    "en": "Hi {first_name}, I'm tier0, Meridian's AI assistant for IT requests.",
    "de": "Hallo {first_name}, ich bin tier0, der KI-Assistent von Meridian für IT-Anfragen.",
    "es": "Hola, {first_name}. Soy tier0, el asistente de IA de Meridian para solicitudes de TI.",
    "fr": "Bonjour {first_name}, je suis tier0, l'assistant IA de Meridian pour les demandes "
    "informatiques.",
}
INTRO_NO_NAME: dict[Language, str] = {
    "en": "Hi, I'm tier0, Meridian's AI assistant for IT requests.",
    "de": "Hallo, ich bin tier0, der KI-Assistent von Meridian für IT-Anfragen.",
    "es": "Hola. Soy tier0, el asistente de IA de Meridian para solicitudes de TI.",
    "fr": "Bonjour, je suis tier0, l'assistant IA de Meridian pour les demandes informatiques.",
}
# Later messages greet by name and skip the disclosure.
GREETING: dict[Language, str] = {
    "en": "Hi {first_name}.",
    "de": "Hallo {first_name}!",
    "es": "¡Hola, {first_name}!",
    "fr": "Bonjour {first_name} !",
}

TEMPLATES: dict[ReplyType, dict[Language, tuple[str, ...]]] = {
    "answer": {
        "en": (
            "Your username is {username}. Use it to sign in to Google Workspace and Okta.",
            "You sign in as {username}. Try it on the Okta sign-in page now; Google Workspace "
            "uses it too.",
            "It's {username}. Try it on the Okta sign-in page, and reply here if it doesn't work.",
        ),
        "de": (
            "Dein Benutzername ist {username}. Damit meldest du dich bei Google Workspace und "
            "Okta an.",
            "Du meldest dich mit {username} an. Probier es gleich auf der Okta-Anmeldeseite; "
            "Google Workspace nutzt ihn auch.",
            "Er lautet {username}. Probier ihn auf der Okta-Anmeldeseite aus und schreib mir "
            "hier, falls es nicht klappt.",
        ),
        "es": (
            "Tu nombre de usuario es {username}. Úsalo para iniciar sesión en Google Workspace "
            "y Okta.",
            "Inicias sesión como {username}. Pruébalo ahora en la página de Okta; Google "
            "Workspace también lo usa.",
            "Es {username}. Pruébalo en la página de inicio de sesión de Okta y escríbeme aquí "
            "si no funciona.",
        ),
        "fr": (
            "Ton nom d'utilisateur est {username}. Utilise-le pour te connecter à Google "
            "Workspace et Okta.",
            "Tu te connectes avec {username}. Essaie tout de suite sur la page Okta ; Google "
            "Workspace l'utilise aussi.",
            "C'est {username}. Essaie-le sur la page de connexion d'Okta et réponds-moi ici si "
            "ça ne marche pas.",
        ),
    },
    "refuse_colleague": {
        "en": (
            "I can only share a username with the person it belongs to. Ask them to message "
            "me directly. If they can't sign in, they can verify their identity at "
            "{recovery_url}.",
            "That's not something I can share: a username only goes to its owner. They can "
            "message me themselves, or verify their identity at {recovery_url} if they're "
            "locked out.",
            "I can't give out someone else's login. Please ask them to DM me. If they can't "
            "get in, they can verify their identity at {recovery_url}.",
        ),
        "de": (
            "Einen Benutzernamen gebe ich nur an die Person, der er gehört. Bitte sie, mir "
            "direkt zu schreiben. Kann sie sich nicht anmelden, bestätigt sie ihre Identität "
            "unter {recovery_url}.",
            "Das kann ich nicht weitergeben: Ein Benutzername geht nur an die Person, der er "
            "gehört. Sie kann mir selbst schreiben oder ihre Identität unter {recovery_url} "
            "bestätigen.",
            "Die Anmeldedaten anderer gebe ich nicht heraus. Bitte die Person, mir eine "
            "Direktnachricht zu schicken. Kommt sie nicht rein, hilft {recovery_url}.",
        ),
        "es": (
            "Solo comparto un nombre de usuario con la persona a la que pertenece. Pídele que "
            "me escriba directamente. Si no puede entrar, puede verificar su identidad en "
            "{recovery_url}.",
            "Eso no lo puedo compartir: un nombre de usuario solo va a su dueño. Esa persona "
            "puede escribirme o verificar su identidad en {recovery_url} si no puede entrar.",
            "No doy los datos de acceso de otra persona. Pídele que me mande un mensaje "
            "directo. Si no puede entrar, puede verificar su identidad en {recovery_url}.",
        ),
        "fr": (
            "Je ne donne un nom d'utilisateur qu'à la personne à qui il appartient. "
            "Demande-lui de m'écrire directement. Si elle ne peut pas se connecter, elle peut "
            "vérifier son identité sur {recovery_url}.",
            "Je ne peux pas partager ça : un nom d'utilisateur ne va qu'à son titulaire. Cette "
            "personne peut m'écrire, ou vérifier son identité sur {recovery_url} si elle est "
            "bloquée.",
            "Je ne donne pas les identifiants de quelqu'un d'autre. Demande à cette personne "
            "de m'écrire en direct. Si elle n'arrive pas à se connecter, elle peut vérifier "
            "son identité sur {recovery_url}.",
        ),
    },
    "unverified": {
        "en": (
            "I can't confirm who you are from this chat, so I can't share account details "
            "here. Please verify your identity at {recovery_url}, and someone from IT will "
            "help you.",
            "I can't tell who you are from here, so I won't share account details. Verify "
            "your identity at {recovery_url} and IT will take it from there.",
            "This chat doesn't prove who you are, so I can't help with account details. "
            "Start at {recovery_url} to verify your identity, and a person from IT will "
            "follow up.",
        ),
        "de": (
            "Über diesen Chat kann ich nicht bestätigen, wer du bist. Deshalb teile ich hier "
            "keine Kontodaten. Bestätige bitte deine Identität unter {recovery_url}, dann "
            "hilft dir jemand aus der IT.",
            "Von hier aus sehe ich nicht, wer du bist, also gebe ich keine Kontodaten heraus. "
            "Bestätige deine Identität unter {recovery_url}, dann übernimmt die IT.",
            "Dieser Chat belegt nicht, wer du bist. Bei Kontodaten kann ich deshalb nicht "
            "helfen. Starte unter {recovery_url} mit der Identitätsprüfung, danach meldet sich "
            "jemand aus der IT.",
        ),
        "es": (
            "Desde este chat no puedo confirmar quién eres, así que no comparto datos de la "
            "cuenta aquí. Verifica tu identidad en {recovery_url} y alguien de TI te ayudará.",
            "Desde aquí no sé quién eres, así que no doy datos de la cuenta. Verifica tu "
            "identidad en {recovery_url} y TI se encargará.",
            "Este chat no demuestra quién eres, así que no puedo ayudarte con datos de la "
            "cuenta. Empieza en {recovery_url} para verificar tu identidad, y alguien de TI "
            "te responderá.",
        ),
        "fr": (
            "Je ne peux pas confirmer qui tu es depuis ce chat, donc je ne partage pas "
            "d'informations de compte ici. Vérifie ton identité sur {recovery_url} et "
            "quelqu'un de l'informatique t'aidera.",
            "D'ici, je ne sais pas qui tu es, donc je ne donne pas d'informations de compte. "
            "Vérifie ton identité sur {recovery_url} et l'équipe informatique prendra le "
            "relais.",
            "Ce chat ne prouve pas qui tu es, donc je ne peux pas t'aider pour ton compte. "
            "Commence par {recovery_url} pour vérifier ton identité, puis quelqu'un de "
            "l'informatique te répondra.",
        ),
    },
    "handoff": {
        "en": (
            "I've passed this to the IT team. Someone will reply here.",
            "This one needs a person, so I've sent it to the IT team. They'll follow up in "
            "this chat.",
            "I've handed this to someone in IT. You'll hear back here.",
        ),
        "de": (
            "Ich habe das an das IT-Team weitergegeben. Jemand antwortet dir hier.",
            "Dafür braucht es einen Menschen, deshalb habe ich es ans IT-Team geschickt. Die "
            "Antwort kommt in diesem Chat.",
            "Ich habe das an jemanden aus der IT übergeben. Du bekommst hier eine Antwort.",
        ),
        "es": (
            "He pasado esto al equipo de TI. Alguien te responderá aquí.",
            "Esto necesita a una persona, así que lo he enviado al equipo de TI. Te "
            "responderán en este chat.",
            "Se lo he pasado a alguien de TI. Recibirás la respuesta aquí.",
        ),
        "fr": (
            "J'ai transmis ta demande à l'équipe informatique. Quelqu'un te répondra ici.",
            "Il faut une personne pour ça, donc je l'ai envoyé à l'équipe informatique. Elle "
            "te répondra dans ce chat.",
            "J'ai passé ça à quelqu'un de l'informatique. Tu auras la réponse ici.",
        ),
    },
    "guard_fallback": {
        "en": (
            "I couldn't answer this one safely, so I've passed it to a person on the IT team.",
            "I'd rather a person checks this one, so I've sent it to the IT team. They'll "
            "reply here.",
            "I couldn't send a safe answer, so someone from IT will pick this up and reply here.",
        ),
        "de": (
            "Das konnte ich nicht sicher beantworten, deshalb habe ich es an jemanden aus dem "
            "IT-Team weitergegeben.",
            "Das sieht sich besser ein Mensch an, deshalb habe ich es ans IT-Team geschickt. "
            "Die Antwort kommt hier.",
            "Ich konnte keine sichere Antwort schicken. Jemand aus der IT übernimmt und "
            "antwortet dir hier.",
        ),
        "es": (
            "No pude responder a esto de forma segura, así que lo he pasado a una persona del "
            "equipo de TI.",
            "Prefiero que lo revise una persona, así que lo he enviado al equipo de TI. Te "
            "responderán aquí.",
            "No pude enviar una respuesta segura. Alguien de TI se encargará y te responderá aquí.",
        ),
        "fr": (
            "Je n'ai pas pu répondre à ça en toute sécurité, donc je l'ai transmis à une "
            "personne de l'équipe informatique.",
            "Je préfère qu'une personne regarde ça, donc je l'ai envoyé à l'équipe "
            "informatique. Elle te répondra ici.",
            "Je n'ai pas pu envoyer de réponse sûre. Quelqu'un de l'informatique va s'en "
            "charger et te répondre ici.",
        ),
    },
    "auto_close": {
        "en": (
            "It's been quiet for 3 days, so I've closed this request. Just reply here if you "
            "still need help.",
            "I've closed this one, since there's been no reply for 3 days. Message me again "
            "any time.",
            "No reply for 3 days, so I've marked this as done. If it isn't sorted, send me a "
            "new message.",
        ),
        "de": (
            "Hier war 3 Tage Ruhe, deshalb habe ich die Anfrage geschlossen. Schreib mir "
            "einfach, wenn du noch Hilfe brauchst.",
            "Ich habe die Anfrage geschlossen, weil 3 Tage keine Antwort kam. Schreib mir "
            "jederzeit wieder.",
            "Nach 3 Tagen ohne Antwort habe ich das als erledigt markiert. Ist noch etwas "
            "offen, schick mir eine neue Nachricht.",
        ),
        "es": (
            "No ha habido respuesta en 3 días, así que he cerrado la solicitud. Escríbeme si "
            "aún necesitas ayuda.",
            "He cerrado esto porque pasaron 3 días sin respuesta. Escríbeme cuando quieras.",
            "Tras 3 días sin respuesta, lo he marcado como resuelto. Si aún falta algo, "
            "mándame un mensaje nuevo.",
        ),
        "fr": (
            "Pas de nouvelles depuis 3 jours, donc j'ai fermé la demande. Réponds-moi si tu "
            "as encore besoin d'aide.",
            "J'ai fermé cette demande, faute de réponse depuis 3 jours. Écris-moi quand tu veux.",
            "Après 3 jours sans réponse, je l'ai marquée comme terminée. S'il reste quelque "
            "chose, envoie-moi un nouveau message.",
        ),
    },
}

# A first name is only used when it looks like one: letters, spaces, hyphens, apostrophes.
_FIRST_NAME = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")
_MAX_FIRST_NAME = 40


def language_for(locale: str | None) -> Language:
    """The reply language for a Slack locale ("de-DE" -> "de"); English otherwise."""
    if not locale:
        return DEFAULT_LANGUAGE
    prefix = re.split(r"[-_]", locale.strip(), maxsplit=1)[0].lower()
    for language in LANGUAGES:
        if prefix == language:
            return language
    return DEFAULT_LANGUAGE


def safe_first_name(name: str | None) -> str | None:
    """The name to greet with, or None if it isn't a plain first name."""
    if name is None:
        return None
    name = name.strip()
    if not name or len(name) > _MAX_FIRST_NAME or not _FIRST_NAME.fullmatch(name):
        return None
    return name


def variant(reply_type: ReplyType, key: str, count: int) -> int:
    """Which variant to use: a stable hash of the reply type and the ticket, not random."""
    digest = hashlib.sha256(f"{reply_type}\x00{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


def body(reply_type: ReplyType, language: Language, key: str, **fields: str) -> str:
    """The reply text for one reply type, with its facts filled in."""
    missing = [f for f in REQUIRED_FIELDS[reply_type] if not fields.get(f)]
    if missing:
        raise ValueError(f"{reply_type} needs {', '.join(missing)}")
    options = TEMPLATES[reply_type][language]
    return options[variant(reply_type, key, len(options))].format(**fields)


def compose(text: str, language: Language, *, first_name: str | None, first: bool) -> str:
    """Put the greeting in front: with the AI disclosure in the first message only."""
    name = safe_first_name(first_name)
    if first:
        header = INTRO[language].format(first_name=name) if name else INTRO_NO_NAME[language]
    elif name:
        header = GREETING[language].format(first_name=name)
    else:
        return text
    return f"{header}\n\n{text}"
