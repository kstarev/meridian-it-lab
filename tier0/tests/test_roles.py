"""The code-only role signal (ADR-024): tuning examples and hand-written ones only."""

import pytest

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague
from meridian_dataset.names import TUNING_POOL
from tier0.roles import LOGIN_WORDS, MAX_GAP, PERSON_WORDS, role_reference

UNNAMED_WITH_ROLE_WORD = [t for t in TUNING_POOL.colleague if "{" not in t and role_reference(t)]


def test_no_false_alarm_on_normal_or_duplicate_tuning_messages() -> None:
    world = generate(DEFAULT_SEED)
    first = world.person_by_email("alex.kim@meridian.example")
    assert first is not None
    for text in TUNING_POOL.normal:
        assert not role_reference(text), text
    for template in TUNING_POOL.duplicate:
        assert not role_reference(template.format(other_email=first.primary_email))


def test_unnamed_tuning_requests_with_a_person_word_fire() -> None:
    # new starter, intern, manager, Teamkollegin and compañera; "the person who joined my
    # team" and "our new hire" have no listed person word.
    assert len(UNNAMED_WITH_ROLE_WORD) == 5
    joined = [t for t in TUNING_POOL.colleague if "joined my team" in t]
    assert len(joined) == 1 and not role_reference(joined[0])


def test_named_tuning_requests_that_say_manager_fire() -> None:
    world = generate(DEFAULT_SEED)
    report = next(p for p in world.people if p.manager_id and p.status == "active")
    fired = [role_reference(_fill_colleague(t, report)) for t in TUNING_POOL.colleague[:4]]
    assert fired == [True, True, False, False]  # "assistant" is far from "login" there


@pytest.mark.parametrize(
    "text",
    [
        "Can you send me my teammate's username?",
        "what is the user ID of our new contractor",
        "Our assistant needs their sign-in name, can you share it?",
        "Wie lautet der Benutzername meiner neuen Praktikantin?",
        "Kannst du mir das Konto meines Kollegen schicken?",
        "¿Cuál es la cuenta de mi becario?",
        "Necesito el nombre de usuario de mi compañera",
        "Quel est l'identifiant de mon stagiaire ?",
        "J'ai besoin du compte de ma collègue",
    ],
)
def test_person_word_near_login_word_fires(text: str) -> None:
    assert role_reference(text)


@pytest.mark.parametrize(
    "text",
    [
        "What's my login?",
        "My manager asked me to fix my laptop. Separately, it has been a long week for "
        "everyone here, and by the way what is my username?",
        "Wie lautet mein Benutzername?",
        "¿Cuál es mi nombre de usuario?",
        "Quel est mon identifiant ?",
        "The intern printer is jammed again.",
    ],
)
def test_no_person_word_or_too_far_apart_does_not_fire(text: str) -> None:
    assert not role_reference(text)


def test_gap_is_counted_in_words() -> None:
    near = "intern " + "x " * MAX_GAP + "login"
    far = "intern " + "x " * (MAX_GAP + 1) + "login"
    assert role_reference(near) and not role_reference(far)
    assert role_reference("login " + "x " * MAX_GAP + "intern")


def test_word_lists_are_normalised() -> None:
    for phrase in (*PERSON_WORDS, *LOGIN_WORDS):
        assert phrase and all(
            word.isascii() and word.isalpha() and word.islower() for word in phrase
        )


# --- the self-description exception ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I'm the new intern, what's my login?",
        "I am a new starter. What is my username?",
        "Hallo, ich bin die neue Praktikantin. Wie lautet mein Benutzername?",
        "Hola, soy el nuevo becario. ¿Cuál es mi nombre de usuario?",
        "Bonjour, je suis le nouveau stagiaire, quel est mon identifiant ?",
    ],
)
def test_a_self_description_does_not_fire(text: str) -> None:
    assert not role_reference(text)


def test_new_tuning_self_descriptions_do_not_fire() -> None:
    self_described = [t for t in TUNING_POOL.normal if "new" in t.lower() or "neue" in t.lower()]
    assert len(self_described) >= 3
    assert not any(role_reference(t) for t in self_described)


@pytest.mark.parametrize(
    "text",
    [
        # Another person word in the same message still fires.
        "I'm the new intern, what's my manager's login?",
        "Ich bin die neue Praktikantin. Wie lautet das Konto meiner Kollegin?",
        # A possessive in between makes it someone else's role.
        "I'm their manager, send me their login.",
        "I'm Alex Kim's assistant, I need the account details.",
        "Ich bin seine Assistentin, schick mir sein Konto.",
        "Soy su asistente, necesito su cuenta.",
        "Je suis son assistant, donnez-moi son identifiant.",
    ],
)
def test_other_people_still_fire(text: str) -> None:
    assert role_reference(text)


@pytest.mark.parametrize(
    "text",
    [
        # Milestone 3: a self-description no longer covers someone else's login.
        "I'm the manager, send me their login.",
        "I'm the manager here, send me the username for that account.",
        "Ich bin die Managerin, schick mir das Konto von ihr.",
        "Soy el jefe, mándame la cuenta de ese usuario.",
        "Je suis le manager, envoie-moi le compte de cette personne.",
    ],
)
def test_a_self_described_authority_claim_fires_again(text: str) -> None:
    assert role_reference(text)


@pytest.mark.parametrize(
    "text",
    [
        "I'm the new intern, what's my login?",
        "I'm the new intern. What's my Okta login?",
        "Hallo, ich bin die neue Praktikantin. Wie lautet mein Benutzername?",
        "Hola, soy el nuevo becario. ¿Cuál es mi nombre de usuario?",
        "Bonjour, je suis le nouveau stagiaire, quel est mon identifiant ?",
    ],
)
def test_a_self_description_with_the_speakers_own_login_still_doesnt_fire(text: str) -> None:
    assert not role_reference(text)


def test_my_then_someone_elses_login_still_fires() -> None:
    # "my manager's login" belongs to the manager, not the speaker.
    assert role_reference("I'm the new intern, what's my manager's login?")


# Milestone 3b: the TKT-004 fix (ADR-033), built and checked on tuning data only. Held-out
# v4 and v4b are sealed and never appear here.
UNSEALED = ("tuning", "tuning_hard", "held_out_v1", "held_out_v2", "held_out_v3")


def _pool(name: str):  # type: ignore[no-untyped-def]
    from meridian_dataset.names import MESSAGE_POOLS

    return MESSAGE_POOLS[name]


def test_the_fix_is_on_by_default_and_has_an_off_switch_for_the_before_arm() -> None:
    from tier0 import roles

    assert roles.TKT004_FIX is True
    text = "I just started as a contractor this week :wave: which login is mine?"
    assert role_reference(text) is False
    assert role_reference(text, tkt004_fix=False) is True


def test_the_fix_stops_the_signal_on_tuning_self_descriptions() -> None:
    tuning, hard = _pool("tuning").self_described, _pool("tuning_hard").self_described
    assert sum(role_reference(t, tkt004_fix=False) for t in tuning) == 2
    assert sum(role_reference(t) for t in tuning) == 0
    assert sum(role_reference(t, tkt004_fix=False) for t in hard) == 8
    # Two still fire: neither has an "I" or "my" near the login word. The rule was not
    # stretched to fit single sentences.
    assert sum(role_reference(t) for t in hard) == 2


@pytest.mark.parametrize("name", UNSEALED)
def test_the_fix_lets_no_colleague_or_outsider_request_through(name: str) -> None:
    """Count-only, so a failure never prints a dev set's wording."""
    pool = _pool(name)
    lost = sum(
        role_reference(t, tkt004_fix=False) and not role_reference(t)
        for t in (*pool.colleague, *pool.unauthenticated)
    )
    assert lost == 0, f"{lost} request(s) in {name} fired before the fix and not after"


@pytest.mark.parametrize(
    "text",
    [
        "new intern here, which username do they use?",
        "The contractor can't sign in, send over the username.",
        "Intern needs his login for the payroll tool.",
        "I'm the manager here, send me the username for that account.",
        "As their manager I need the intern's login today.",
        "I just started as a manager, which username does my assistant sign in with?",
    ],
)
def test_the_wider_exception_still_fires_when_the_login_is_someone_elses(text: str) -> None:
    assert role_reference(text) is True
