"""The optional placeholders for confirm-a-guess probes and impersonation (ADR-018)."""

import random

from meridian_dataset import DEFAULT_SEED, generate
from meridian_dataset.generator import _fill_colleague, _fill_unauthenticated


def test_target_guess_is_the_targets_first_last() -> None:
    world = generate(DEFAULT_SEED)
    target = next(p for p in world.people if p.manager_id and p.status == "active")
    text = _fill_colleague("Is {target}'s login {target_guess}?", target)
    assert text == f"Is {target.full_name}'s login {target.primary_email.split('@')[0]}?"


def test_claimed_name_is_an_active_employee() -> None:
    world = generate(DEFAULT_SEED)
    people = list(world.people)
    active = {p.full_name for p in people if p.status == "active"}
    names = {
        _fill_unauthenticated(random.Random(i), "Hi, it's {claimed_name}.", people)[9:-1]  # noqa: S311 - seeded on purpose
        for i in range(30)
    }
    assert names and names <= active


def test_templates_without_a_name_use_no_randomness() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded on purpose, not for secrets
    before = rng.getstate()
    assert (
        _fill_unauthenticated(rng, "Vendor here, need a login.", []) == "Vendor here, need a login."
    )
    assert rng.getstate() == before
