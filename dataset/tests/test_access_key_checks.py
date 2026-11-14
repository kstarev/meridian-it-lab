"""The answer-key checks (AAC-009): they pass on both worlds and catch a broken key.

The held-out world is checked count-only: each assertion names a check, never content.
"""

import json
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from meridian_dataset.access import AccessWorld, generate_access, load_recipe
from meridian_dataset.access.key_checks import CHECKS, check_key, total
from meridian_dataset.access.model import AccessFinding
from meridian_dataset.access.recipe import Recipe
from meridian_dataset.access.recipe_tools import main as tools

RECIPES = Path(__file__).resolve().parents[1] / "src/meridian_dataset/access/recipes"


@cache
def _tuning() -> tuple[Recipe, AccessWorld]:
    recipe = load_recipe("tuning")
    assert recipe.seed is not None
    return recipe, generate_access(recipe.seed, recipe)


@cache
def _held_out_problems() -> dict[str, int]:
    recipe = load_recipe("held_out_a1")
    assert recipe.seed is not None
    return check_key(generate_access(recipe.seed, recipe), recipe)


def _with_findings(findings: tuple[AccessFinding, ...]) -> dict[str, int]:
    recipe, world = _tuning()
    key = replace(world.answer_key, findings=findings)
    return check_key(replace(world, answer_key=key), recipe)


def _findings() -> tuple[AccessFinding, ...]:
    return _tuning()[1].answer_key.findings


def _first(kind: str) -> AccessFinding:
    return next(f for f in _findings() if f.type == kind)


def _without(kind: str) -> tuple[AccessFinding, ...]:
    return tuple(f for f in _findings() if f.type != kind)


def test_tuning_key_passes_every_check() -> None:
    recipe, world = _tuning()
    problems = check_key(world, recipe)
    assert set(problems) == set(CHECKS)
    assert total(problems) == 0, {k: v for k, v in problems.items() if v}


@pytest.mark.parametrize("check", CHECKS)
def test_held_out_key_passes(check: str) -> None:
    assert _held_out_problems()[check] == 0, f"held_out_a1 fails the {check} check"


def test_a_duplicate_finding_is_caught() -> None:
    finding = _first("drift")
    assert _with_findings((*_findings(), finding))["duplicate_findings"] == 1


def test_a_finding_about_a_missing_person_is_caught() -> None:
    bad = replace(_first("mover_stale_access"), employee_id="E99999")
    problems = _with_findings((*_findings(), bad))
    assert problems["finding_person_missing"] >= 1


def test_a_finding_about_a_missing_account_is_caught() -> None:
    bad = replace(_first("orphaned_account"), account="nobody-at-all@meridian.example")
    findings = tuple(bad if f == _first("orphaned_account") else f for f in _findings())
    assert _with_findings(findings)["finding_account_missing"] == 1


def test_contradicting_findings_are_caught() -> None:
    drift = _first("drift")
    other = replace(drift, type="orphaned_account", expected_action="flag")
    assert _with_findings((*_findings(), other))["contradicting_findings"] == 1


def test_a_missing_mass_termination_is_caught() -> None:
    problems = _with_findings(_without("hris_mass_termination"))
    assert problems["bulk_import_not_in_key"] == 1
    assert problems["count_mismatch:hris_mass_termination"] == 1


def test_missing_break_glass_is_caught() -> None:
    assert _with_findings(_without("break_glass"))["break_glass_not_in_key"] == 3


def test_a_missing_hand_grant_is_caught() -> None:
    problems = _with_findings(_without("drift"))
    assert problems["hand_grant_not_in_key"] >= 1
    assert problems["count_mismatch:drift"] == 1


def test_missing_leave_findings_are_caught() -> None:
    problems = _with_findings(_without("leave_of_absence"))
    assert problems["on_leave_active_account_not_in_key"] >= 1
    assert problems["count_mismatch:leave_of_absence"] == 1


def test_missing_leaver_findings_are_caught() -> None:
    problems = _with_findings(_without("leaver_access_left"))
    assert problems["terminated_active_account_not_in_key"] >= 1


def test_missing_contractor_findings_are_caught() -> None:
    problems = _with_findings(_without("contractor_past_end"))
    assert problems["past_end_contractor_not_in_key"] >= 1


def test_an_account_without_an_owner_is_caught() -> None:
    recipe, world = _tuning()
    owners = world.answer_key.owners[1:]
    key = replace(world.answer_key, owners=owners)
    assert check_key(replace(world, answer_key=key), recipe)["account_without_owner"] == 1


def test_check_and_seal_refuse_a_broken_key(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A custom case leaves a terminated person's active account out of the key."""
    obj = json.loads((RECIPES / "tuning.json").read_text(encoding="utf-8"))
    obj["name"] = "test_broken_key"
    obj["custom_cases"] = [
        {
            "id": "forgotten",
            "description": "A leaver whose Slack account nobody listed.",
            "people": [
                {
                    "ref": "p",
                    "given_name": "Test",
                    "family_name": "Forgotten",
                    "role_id": "sales-ae",
                    "employment_type": "employee",
                    "country": "DE",
                    "status": "terminated",
                    "start_date": "2024-01-15",
                    "end_date": "2026-10-01",
                }
            ],
            "accounts": [{"ref": "p", "system": "slack", "state": "active"}],
            "findings": [],
        }
    ]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    assert tools(["check", str(path)]) == 1
    assert "terminated_active_account_not_in_key" in capsys.readouterr().err
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    assert tools(["seal", str(path), "--name", "held_out_test_broken"], recipes) == 1
    assert not any(recipes.iterdir())


def _with_world(**changes: object) -> dict[str, int]:
    recipe, world = _tuning()
    return check_key(replace(world, **changes), recipe)


def test_an_orphaned_okta_users_missing_app_finding_is_caught() -> None:
    gone = next(
        f for f in _findings() if f.type == "orphaned_account" and f.system == "okta" and f.app_id
    )
    problems = _with_findings(tuple(f for f in _findings() if f != gone))
    assert problems["orphan_app_access_not_in_key"] == 1
    assert problems["count_mismatch:orphaned_account"] == 0  # app findings are not accounts


def test_a_missing_break_glass_system_is_caught() -> None:
    gone = next(f for f in _findings() if f.type == "break_glass" and f.system == "google")
    problems = _with_findings(tuple(f for f in _findings() if f != gone))
    assert problems["break_glass_system_not_in_key"] == 1
    assert problems["break_glass_not_in_key"] == 0  # the identity is still keyed
    assert problems["count_mismatch:break_glass"] == 0


def test_a_privilege_change_on_an_unassigned_app_is_caught() -> None:
    _, world = _tuning()
    event = next(e for e in world.okta.log if e.event_type.endswith("change_privilege"))
    assignments = tuple(
        a
        for a in world.okta.assignments
        if not (a.app_id == event.target_app_id and a.user_id == event.target_user_id)
    )
    okta = replace(world.okta, assignments=assignments)
    assert _with_world(okta=okta)["privilege_drift_on_unassigned_app"] == 1
