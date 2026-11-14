"""recipe_tools: catalog, check (counts only) and seal (deterministic, to an injected dir)."""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.access import catalog as cat
from meridian_dataset.access.recipe import available_recipes, canonical_json, load_recipe
from meridian_dataset.access.recipe_tools import main

EXTRA_A = "Zyxwvut"
EXTRA_B = "Qponmlk"


@pytest.fixture
def recipe_path(tuning_object: dict[str, Any], tmp_path: Path) -> Path:
    tuning_object["name"] = "test_tools"
    tuning_object["names"]["extra_given"] = [EXTRA_A]
    tuning_object["names"]["extra_family"] = [EXTRA_B]
    tuning_object["names"]["extra_share"] = 0.3
    path = tmp_path / "test_tools.json"
    path.write_text(json.dumps(tuning_object), encoding="utf-8")
    return path


def test_catalog_prints_the_vocabulary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["catalog"]) == 0
    out = capsys.readouterr().out
    for role in cat.ROLES:
        assert role.role_id in out and role.title in out
    for word in (*cat.FINDING_TYPES, *cat.ACTIONS, *cat.EVENT_KINDS, *cat.EXPORT_FIELDS):
        assert word in out
    for app in cat.APPS:
        assert app.app_id in out
    assert "2026-12-01" in out and "2026-11-01" in out and "Engineering (" in out
    assert "{first}" in out and "last_comma_first" in out
    assert "Müller" not in out and "orphaned_accounts" not in out


def test_check_prints_counts_only(recipe_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(recipe_path)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "ok" and lines[1].startswith("digest: ") and len(lines[1]) == 8 + 64
    assert "people: 268" in out and "exports: 95" in out and "layouts used: 4 of 4" in out
    assert "orphaned_account: 34" in out
    assert EXTRA_A not in out and EXTRA_B not in out
    assert "@" not in out


def test_check_reports_an_error_and_exits_one(
    tuning_object: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tuning_object["bogus"] = 1
    path = tmp_path / "test_bad.json"
    path.write_text(json.dumps(tuning_object), encoding="utf-8")
    assert main(["check", str(path)]) == 1
    assert "unknown key" in capsys.readouterr().err


def test_seal_writes_a_deterministic_gzip(
    recipe_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real_before = available_recipes()
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    assert main(["seal", str(recipe_path), "--name", "held_out_test_x"], first) == 0
    out = capsys.readouterr().out
    assert "held_out_test_x.json.gz" in out and "digest: " in out
    assert EXTRA_A not in out and EXTRA_B not in out
    assert main(["seal", str(recipe_path), "--name", "held_out_test_x"], second) == 0
    one, two = first / "held_out_test_x.json.gz", second / "held_out_test_x.json.gz"
    assert one.read_bytes() == two.read_bytes()
    obj = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert gzip.decompress(one.read_bytes()) == canonical_json(obj)
    assert one.read_bytes()[4:8] == b"\x00\x00\x00\x00"  # gzip mtime is zero
    loaded = load_recipe("held_out_test_x", first)
    assert loaded.name == "test_tools" and loaded.digest in out
    assert available_recipes() == real_before  # the real recipes directory is untouched


def test_seal_refuses_bad_names_and_existing_files(
    recipe_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("tuning", "other_name", "held_out", "held_out_a/b"):
        assert main(["seal", str(recipe_path), "--name", name], tmp_path) == 1
    assert not list(tmp_path.glob("*.gz"))
    assert main(["seal", str(recipe_path), "--name", "held_out_test_y"], tmp_path) == 0
    assert main(["seal", str(recipe_path), "--name", "held_out_test_y"], tmp_path) == 1
    assert "already exists" in capsys.readouterr().err


def test_seal_refuses_an_invalid_recipe(
    tuning_object: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tuning_object["headcount"] = 5
    path = tmp_path / "test_bad.json"
    path.write_text(json.dumps(tuning_object), encoding="utf-8")
    assert main(["seal", str(path), "--name", "held_out_test_z"], tmp_path) == 1
    assert not (tmp_path / "held_out_test_z.json.gz").exists()
    assert "headcount" in capsys.readouterr().err
