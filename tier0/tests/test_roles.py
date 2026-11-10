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
    # new starter, intern and manager; "the person who joined my team" has no person word.
    assert len(UNNAMED_WITH_ROLE_WORD) == 3
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
