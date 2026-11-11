"""The held-out v3 world (ADR-029): one world that uses every template once.

Assertions are counts and booleans only, so a failure never prints v3 wording.
"""

import math
import re

import pytest

from meridian_dataset import HOLDOUT_V3_SEED, World, generate
from meridian_dataset.generator import AI_DISCLOSURE_FACT, REFERENCE_TIME
from meridian_dataset.names import HELD_OUT_V3_POOL

CASES = [(HOLDOUT_V3_SEED, None), *((seed, "held_out_v3") for seed in (0, 1, 2, 3, 4))]
ALEX_FIRST = "alex.kim@meridian.example"


@pytest.fixture(params=CASES, ids=lambda case: f"{case[0]}-{case[1] or 'default'}")
def world(request: pytest.FixtureRequest) -> World:
    seed, split = request.param
    return generate(seed, split=split)


def _by_scenario(world: World, scenario: str) -> list[tuple[object, object]]:
    return [
        (t, world.answer_key.entry(t.id))
        for t in world.tickets
        if world.answer_key.entry(t.id).scenario == scenario
    ]


def test_shape_and_order(world: World) -> None:
    assert world.message_pool == "held_out_v3"
    assert len(world.tickets) == 54
    assert [t.id for t in world.tickets] == [f"TKT-{n:03d}" for n in range(1, 55)]
    times = [t.received_at for t in world.tickets]
    assert times == sorted(set(times))
    assert times[0] > REFERENCE_TIME.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_scenario_counts(world: World) -> None:
    scenarios = [world.answer_key.entry(t.id).scenario for t in world.tickets]
    counts = {s: scenarios.count(s) for s in set(scenarios)}
    assert counts == {
        "normal": 30,
        "duplicate_name": 6,
        "colleague_lookup": 12,
        "unauthenticated": 6,
    }


def test_every_template_is_used_once(world: World) -> None:
    texts = [t.text for t in world.tickets]
    assert len(set(texts)) == 54
    pool = HELD_OUT_V3_POOL
    plain = [
        *pool.normal,
        *pool.self_described,
        *(t for t in pool.unauthenticated if "{claimed_name}" not in t),
    ]
    unused_or_repeated = sum(texts.count(template) != 1 for template in plain)
    assert unused_or_repeated == 0, f"{unused_or_repeated} plain template(s) not used once"
    filled = [
        *((t, "other_email") for t in pool.duplicate),
        *((t, "target") for t in pool.colleague),
        *((t, "claimed_name") for t in pool.unauthenticated if "{claimed_name}" in t),
    ]
    for template, key in filled:
        pattern = re.escape(template)
        for name in ("other_email", "target", "target_guess", "claimed_name"):
            pattern = pattern.replace(re.escape("{" + name + "}"), ".+?")
        matches = sum(re.fullmatch(pattern, x, re.S) is not None for x in texts)
        assert matches == 1, f"{key} template matched {matches} times"
    assert len(plain) + len(filled) == 54


def test_probe_placeholders_are_covered() -> None:
    pool = HELD_OUT_V3_POOL
    assert sum("{target_guess}" in t for t in pool.colleague) >= 2
    assert sum("{claimed_name}" in t for t in pool.unauthenticated) >= 1


def test_normal_senders(world: World) -> None:
    eligible = [
        p
        for p in world.people
        if p.status == "active" and p.primary_email not in ("alex.kim2@meridian.example",)
    ]
    leaver = next(p for p in world.people if p.status == "suspended")
    alex_second = world.person_by_email("alex.kim2@meridian.example")
    assert alex_second is not None
    senders = [
        world.person_by_slack_id(t.sender_slack_user_id) for t, _ in _by_scenario(world, "normal")
    ]
    assert len(senders) == 30
    bad = sum(
        s is None
        or s.status != "active"
        or s.directory_id in (leaver.directory_id, alex_second.directory_id)
        for s in senders
    )
    assert bad == 0, f"{bad} bad normal sender(s)"
    counts = [sum(s == x for s in senders) for x in set(senders)]
    assert max(counts) <= math.ceil(30 / len(eligible))


def test_duplicate_tickets(world: World) -> None:
    alex_second = world.person_by_email("alex.kim2@meridian.example")
    assert alex_second is not None
    rows = _by_scenario(world, "duplicate_name")
    assert len(rows) == 6
    bad = sum(
        t.sender_slack_user_id != alex_second.slack_user_id or ALEX_FIRST not in t.text
        for t, _ in rows
    )
    assert bad == 0, f"{bad} bad duplicate ticket(s)"


def test_colleague_tickets(world: World) -> None:
    rows = _by_scenario(world, "colleague_lookup")
    assert len(rows) == 12
    bad = 0
    for ticket, entry in rows:
        manager = world.person_by_slack_id(ticket.sender_slack_user_id)
        targets = [
            p
            for p in world.people
            if manager is not None
            and p.manager_id == manager.directory_id
            and p.full_name in ticket.text
        ]
        ok = (
            manager is not None
            and entry.requester_directory_id == manager.directory_id
            and (len(targets) >= 1 or not any(p.full_name in ticket.text for p in world.people))
            and all(p.primary_email in entry.forbidden_facts for p in targets)
        )
        bad += not ok
    assert bad == 0, f"{bad} bad colleague ticket(s)"


def test_unauthenticated_tickets(world: World) -> None:
    (external,) = world.external_slack_users
    rows = _by_scenario(world, "unauthenticated")
    assert len(rows) == 6
    bad = sum(
        t.sender_slack_user_id != external.slack_user_id or t.sender_team_id != external.team_id
        for t, _ in rows
    )
    assert bad == 0, f"{bad} bad unauthenticated ticket(s)"


def test_answer_key(world: World) -> None:
    entries = world.answer_key.entries
    assert [e.ticket_id for e in entries] == [t.id for t in world.tickets]
    bad = 0
    for e in entries:
        bad += bool(set(e.required_facts) & set(e.forbidden_facts))
        if e.requester_directory_id is not None:
            requester = world.person_by_id(e.requester_directory_id)
            bad += requester is None or requester.primary_email in e.forbidden_facts
        if e.scenario in ("colleague_lookup", "unauthenticated"):
            bad += e.attack != "ASI03" or e.allowed_tools != ()
        else:
            bad += e.attack is not None or AI_DISCLOSURE_FACT not in e.required_facts
    assert bad == 0, f"{bad} answer key problem(s)"


def test_unnamed_colleague_templates_present() -> None:
    unnamed = sum("{target" not in t for t in HELD_OUT_V3_POOL.colleague)
    assert unnamed == 6, f"only {unnamed} unnamed colleague template(s)"


def test_confirm_guess_count_matches_probe_templates(world: World) -> None:
    flagged = sum(e.confirm_guess for e in world.answer_key.entries)
    probes = sum("{target_guess}" in t for t in HELD_OUT_V3_POOL.colleague)
    assert flagged == probes
    assert all(
        e.scenario == "colleague_lookup" for e in world.answer_key.entries if e.confirm_guess
    )


def test_pool_field_sizes() -> None:
    pool = HELD_OUT_V3_POOL
    assert len(pool.normal) == 24
    assert len(pool.self_described) == 6
    assert len(pool.duplicate) == 6
    assert len(pool.colleague) == 12
    assert len(pool.unauthenticated) == 6
    assert all(t.count("{other_email}") == 1 for t in pool.duplicate)
    named = [t for t in pool.colleague if "{target}" in t]
    assert len(named) == 6
    assert all(t.count("{target}") == 1 for t in named)
    assert all("{target" not in t for t in pool.colleague[6:])


def test_self_described_templates_present() -> None:
    assert len(HELD_OUT_V3_POOL.self_described) == 6


def test_v3_flags_its_self_descriptions_and_older_worlds_carry_no_flag(tmp_path) -> None:
    """Count-only: v3's wording is never read here (ADR-029)."""
    import json

    from meridian_dataset import HOLDOUT_SEED, HOLDOUT_V3_SEED, generate, write_dataset
    from meridian_dataset.generator import HOLDOUT_V2_SEED

    v3 = generate(HOLDOUT_V3_SEED)
    assert sum(e.self_described for e in v3.answer_key.entries) == 6
    assert all(e.scenario == "normal" for e in v3.answer_key.entries if e.self_described)
    for seed in (HOLDOUT_SEED, HOLDOUT_V2_SEED):
        paths = write_dataset(generate(seed), tmp_path / str(seed))
        entries = json.loads(paths["answer_key.json"].read_text())["entries"]
        assert all("self_described" not in e for e in entries)
