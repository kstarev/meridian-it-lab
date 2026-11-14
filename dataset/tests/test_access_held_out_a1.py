"""The sealed held-out recipe, held_out_a1 (AAC-005, AAC-009). Count-only and yes/no checks.

A blind helper wrote this recipe without seeing Access-as-Code code. The builder never reads it.
Every check here is a function that returns a bool or a number, asserted on its own, so a failure
says which check failed and never prints the recipe's content (no recipe value is ever an
assertion operand).
"""

import gzip
from functools import cache
from pathlib import Path

from meridian_dataset.access import AccessWorld, generate_access, load_recipe
from meridian_dataset.access.__main__ import main
from meridian_dataset.access.export import write_access
from meridian_dataset.access.recipe import HELD_OUT_A1_SHA256, Recipe, available_recipes

NAME = "held_out_a1"
RECIPES = Path(__file__).resolve().parents[1] / "src/meridian_dataset/access/recipes"
KNOWN_SEEDS = {1108, 2611, 3107, 4417, 6108}  # tier0's four worlds and the access tuning world


@cache
def _held_out() -> Recipe:
    return load_recipe(NAME)


@cache
def _tuning() -> Recipe:
    return load_recipe("tuning")


@cache
def _world() -> AccessWorld:
    recipe = _held_out()
    assert recipe.seed is not None
    return generate_access(recipe.seed, recipe)


# The seal.


def _digest_matches() -> bool:
    return _held_out().digest == HELD_OUT_A1_SHA256


def _stored_compressed_only() -> bool:
    plain = sorted(p.name for p in RECIPES.glob("held_out*.json"))
    return (RECIPES / f"{NAME}.json.gz").is_file() and not plain


def _gzip_holds_canonical_json() -> bool:
    data = gzip.decompress((RECIPES / f"{NAME}.json.gz").read_bytes())
    return data.startswith(b"{") and b"\n" not in data


def test_sealed_by_hash() -> None:
    assert _digest_matches(), "held_out_a1 changed: its digest no longer matches the seal"


def test_stored_only_compressed() -> None:
    assert _stored_compressed_only(), "held_out_a1 must exist only as held_out_a1.json.gz"
    assert _gzip_holds_canonical_json(), "held_out_a1.json.gz is not canonical JSON"
    assert NAME in available_recipes()


def test_cli_refuses_without_the_final_score_flag(tmp_path: Path) -> None:
    code = main(["--recipe", NAME, "--out", str(tmp_path)])
    assert code == 2
    assert not any(tmp_path.iterdir()), "nothing may be written without --final-score"


# Counts.


def _seed_is_new() -> bool:
    return _held_out().seed not in KNOWN_SEEDS


def _headcount_is_230() -> bool:
    return _held_out().headcount == 230 and len(_world().answer_key.true_roles) == 230


def _has_95_exports() -> bool:
    return len(_world().exports) == 95


def _same_seed_same_world() -> bool:
    recipe = _held_out()
    assert recipe.seed is not None
    return generate_access(recipe.seed, recipe) == _world()


def _counts_differ_from_tuning() -> bool:
    fields = ("orphaned_accounts", "movers", "leavers_with_access", "drift", "leave_of_absence")
    return any(getattr(_held_out(), f) != getattr(_tuning(), f) for f in fields)


def _has_mass_termination() -> bool:
    return _held_out().mass_termination.events == 1


def test_counts() -> None:
    assert _seed_is_new(), "held_out_a1 must use a seed no other world uses"
    assert _headcount_is_230(), "held_out_a1 must be the same 230-person company"
    assert _has_95_exports(), "held_out_a1 must have 95 exports"
    assert _counts_differ_from_tuning(), "held_out_a1's planted counts must not copy tuning's"
    assert _has_mass_termination(), "held_out_a1 must keep a mass-termination event"


def test_same_seed_same_world() -> None:
    assert _same_seed_same_world(), "held_out_a1 must generate deterministically"


def test_writes_files(tmp_path: Path) -> None:
    paths = write_access(_world(), tmp_path)
    assert len([p for p in paths if p.startswith("exports/")]) == 95


# New kinds of mess the tuning world doesn't have (AAC-009).


def _headers(recipe: Recipe) -> set[str]:
    return {c.header for layout in recipe.export_layouts for c in layout.columns}


def _new_layouts() -> int:
    tuning = _headers(_tuning())
    return sum(
        1
        for layout in _held_out().export_layouts
        if any(c.header not in tuning for c in layout.columns)
    )


def _new_date_formats() -> int:
    tuning = {(d.strftime, d.special) for d in _tuning().date_formats}
    return sum(1 for d in _held_out().date_formats if (d.strftime, d.special) not in tuning)


def _new_special_date_formats() -> int:
    tuning = {d.special for d in _tuning().date_formats if d.special}
    return sum(1 for d in _held_out().date_formats if d.special and d.special not in tuning)


def _new_username_patterns() -> int:
    return len(set(_held_out().username_patterns) - set(_tuning().username_patterns))


def _new_names() -> int:
    held, tuning = _held_out().names, _tuning().names
    return len(set(held.extra_given) - set(tuning.extra_given)) + len(
        set(held.extra_family) - set(tuning.extra_family)
    )


def _new_encodings() -> int:
    tuning = {codec for codec, _ in _tuning().exports.encodings}
    return sum(1 for codec, _ in _held_out().exports.encodings if codec not in tuning)


def _has_duplicate_rows() -> bool:
    return _held_out().exports.noise.duplicate_row_share > 0


def _custom_cases() -> int:
    return len(_held_out().custom_cases)


def _every_case_has_findings() -> bool:
    return all(case.findings for case in _held_out().custom_cases)


def test_new_mess() -> None:
    assert _new_layouts() >= 2, "need at least 2 layouts with headers tuning doesn't use"
    assert _new_date_formats() >= 2, "need at least 2 date formats tuning doesn't use"
    assert _new_special_date_formats() >= 1, "need at least 1 special date format"
    assert _new_username_patterns() >= 2, "need at least 2 username patterns tuning doesn't use"
    assert _new_names() >= 1, "need new name spellings"
    assert _new_encodings() >= 1, "need an encoding tuning doesn't use"
    assert _has_duplicate_rows(), "need duplicate rows"


def test_new_hr_edge_cases() -> None:
    assert _custom_cases() >= 5, "need at least 5 custom HR edge cases"
    assert _every_case_has_findings(), "every custom case must state its expected findings"
