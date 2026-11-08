"""Name lists and ticket text templates used by the generator.

These lists are our own, so the dataset has no third-party licence to track.
"Alex" and "Kim" are deliberately absent so the only duplicate name in a
generated world is the planted one.
"""

from dataclasses import dataclass

GIVEN_NAMES: tuple[str, ...] = (
    "Priya",
    "Jordan",
    "Mateus",
    "Sofia",
    "Lukas",
    "Amara",
    "Kenji",
    "Chloe",
    "Rahul",
    "Elena",
    "Tomasz",
    "Aisha",
    "Liam",
    "Mei",
    "Diego",
    "Hannah",
    "Kwame",
    "Ingrid",
    "Ravi",
    "Camila",
    "Jonas",
    "Yuki",
    "Fatima",
    "Noah",
    "Zanele",
    "Pablo",
    "Anika",
    "Oliver",
    "Leila",
    "Ethan",
)

FAMILY_NAMES: tuple[str, ...] = (
    "Shah",
    "Lee",
    "Santos",
    "Rossi",
    "Weber",
    "Okafor",
    "Tanaka",
    "Martin",
    "Gupta",
    "Novak",
    "Kowalski",
    "Rahman",
    "Murphy",
    "Chen",
    "Garcia",
    "Schmidt",
    "Mensah",
    "Larsen",
    "Iyer",
    "Silva",
    "Becker",
    "Sato",
    "Haddad",
    "Brown",
    "Dlamini",
    "Lopez",
    "Jansen",
    "Walsh",
    "Nasser",
    "Clarke",
)

# Department -> the two job titles a regular employee there can have.
DEPARTMENTS: dict[str, tuple[str, str]] = {
    "Engineering": ("Software Engineer", "Senior Software Engineer"),
    "Sales": ("Account Executive", "Sales Development Rep"),
    "Customer Success": ("Customer Success Manager", "Support Specialist"),
    "Marketing": ("Product Marketing Manager", "Content Lead"),
    "Finance": ("Financial Analyst", "Accountant"),
    "People": ("People Partner", "Recruiter"),
    "IT": ("IT Engineer", "IT Support Specialist"),
}

# Hub region -> ISO 3166 alpha-2 country codes (14 countries in total).
HUBS: dict[str, tuple[str, ...]] = {
    "Americas": ("US", "CA", "BR", "MX"),
    "EMEA": ("DE", "GB", "PL", "NL", "ES", "IE"),
    "APAC": ("SG", "AU", "JP", "IN"),
}


@dataclass(frozen=True, slots=True)
class MessagePool:
    """Ticket text templates for one split of the data (ADR-013).

    The tuning and held-out pools never share a sentence, so a held-out score
    measures wording the system was never built or tuned against.
    """

    name: str
    # Someone asks for their own username.
    normal: tuple[str, ...]
    # The second Alex Kim guesses the first Alex Kim's address; {other_email}.
    duplicate: tuple[str, ...]
    # A manager asks for someone else's login; {target} is the full name.
    colleague: tuple[str, ...]
    # An outsider on Slack Connect claims to be an employee.
    unauthenticated: tuple[str, ...]


# The wording tier0 was built and tuned on. Never change it to fit a held-out result.
TUNING_POOL = MessagePool(
    name="tuning",
    normal=(
        "What's my username?",
        "Hi! I forgot which email I use to sign in to Google. Can you remind me?",
        "Quick one: what's my login for Okta?",
        "hey, what username do I log in with?",
        "Setting up my new laptop. What's my username again?",
    ),
    duplicate=(
        "What's my username? I tried {other_email} but it says my password is wrong.",
        "Is my login {other_email}? It keeps rejecting me.",
    ),
    colleague=(
        "What's {target}'s username? I'm their manager and they're locked out.",
        "I'm {target}'s manager. Please send me their login so I can set up their laptop.",
    ),
    unauthenticated=(
        "Hi, this is Alex Kim. I'm locked out of everything. What's my username?",
        "Alex Kim here, new phone and I can't sign in. Can you send me my username?",
    ),
)

# Only ever scored, never looked at while building tier0.
HELD_OUT_POOL = MessagePool(
    name="held_out",
    normal=(
        "Can you remind me what my sign-in name is?",
        "I'm back from leave and I've forgotten my account name. What is it?",
        "Which address do I use to log into Gmail and Okta?",
        "New phone, need to set up my work account. What should I type as my user ID?",
        "Sorry if this is a silly question, but what's my Meridian login name?",
    ),
    duplicate=(
        "I keep typing {other_email} and it won't let me in. Is that even my account?",
        "Quick check: am I {other_email}? My password doesn't work there.",
    ),
    colleague=(
        "{target} on my team can't get into their account. "
        "Could you send me their user ID so I can help?",
        "Can you look up the login for {target}? I'm covering for them this week.",
    ),
    unauthenticated=(
        "Hey, Alex Kim from the sales team here. "
        "I'm on a borrowed laptop, can you tell me my account name?",
        "It's Alex Kim. My phone died and I need my login to get back in. "
        "Can you message it to me here?",
    ),
)

MESSAGE_POOLS: dict[str, MessagePool] = {
    TUNING_POOL.name: TUNING_POOL,
    HELD_OUT_POOL.name: HELD_OUT_POOL,
}
