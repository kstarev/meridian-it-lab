"""The 95 messy exports, the answer-key separation and the command line."""

import codecs
import csv
import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from meridian_dataset.access import AccessWorld
from meridian_dataset.access import catalog as cat
from meridian_dataset.access.__main__ import main

_STRPTIME = {
    "iso": "%Y-%m-%d",
    "dmy_slash": "%d/%m/%Y",
    "mdy_slash": "%m/%d/%Y",
    "d_mon_y": "%d %b %Y",
}
_DATE_COLUMN = {"A": 3, "B": 3, "C": 3, "D": 4}


def test_layouts_formats_and_username_only(access_world: AccessWorld) -> None:
    exports = access_world.exports
    assert {e.layout for e in exports} == {"A", "B", "C", "D"}
    assert {e.date_format for e in exports} == set(_STRPTIME)
    assert sum(e.layout == "D" for e in exports) == 20
    for e in exports:
        if e.layout == "D":
            assert e.header[0] == "Username"
            assert not any("@" in row[0] for row in e.rows)
        else:
            assert all("@" in row[0] for row in e.rows if any(row))


def test_encodings_and_mess(access_world: AccessWorld, access_files: dict[str, Path]) -> None:
    legacy = [e for e in access_world.exports if e.encoding == "cp1252"]
    assert len(legacy) == 3
    for e in legacy:
        raw = access_files[f"exports/{e.file_name}"].read_bytes()
        text = raw.decode("cp1252")
        assert not text.isascii()
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
    boms = [
        e for e in access_world.exports
        if access_files[f"exports/{e.file_name}"].read_bytes().startswith(codecs.BOM_UTF8)
    ]  # fmt: skip
    assert boms and all(e.encoding == "utf-8-sig" for e in boms)
    assert sum(1 for e in access_world.exports for row in e.rows if not any(row)) >= 1
    rows = [row for e in access_world.exports for row in e.rows if e.layout != "D" and any(row)]
    assert any(row[0] != row[0].strip() for row in rows)  # trailing space
    assert any(row[0] != row[0].lower() for row in rows)  # mixed case


def test_files_read_back_and_dates_parse(
    access_world: AccessWorld, access_files: dict[str, Path]
) -> None:
    for e in access_world.exports:
        raw = access_files[f"exports/{e.file_name}"].read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
        read = list(csv.reader(io.StringIO(raw.decode(e.encoding), newline="")))
        assert read[0] == list(e.header)
        assert [tuple(r) for r in read[1:]] == list(e.rows)
        for row in e.rows:
            if any(row):
                datetime.strptime(row[_DATE_COLUMN[e.layout]], _STRPTIME[e.date_format])


def test_answer_key_fields_stay_in_the_answer_key(
    access_world: AccessWorld, access_files: dict[str, Path]
) -> None:
    private = ["expected_action", "true_role", "orphaned_account", "mover_stale_access"]
    private += ["break_glass", "recipe_sha256", *(r.role_id for r in cat.ROLES)]
    for name, path in access_files.items():
        text = path.read_bytes().decode("latin-1")
        if name == "answer_key.json":
            assert "expected_action" in text and "true_roles" in text
        else:
            assert [s for s in private if s in text] == [], name
    key = json.loads(access_files["answer_key.json"].read_text(encoding="utf-8"))
    assert key["recipe_name"] == "tuning" and len(key["recipe_sha256"]) == 64


def test_job_titles_are_messy_variants(access_world: AccessWorld) -> None:
    variants = {v for r in cat.ROLES for v in r.variants}
    titles = {r.job_title for r in access_world.hris.records}
    assert titles <= variants and len(titles) > 40


def test_cli_writes_the_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--seed", "6108", "--out", str(tmp_path)]) == 0
    out = tmp_path / "seed-6108"
    assert (out / "hris.json").is_file() and (out / "manifest.json").is_file()
    assert len(list((out / "exports").glob("*.csv"))) == 95
    printed = capsys.readouterr().out
    assert "orphaned_account: 34" in printed and "manifest sha256" in printed


def test_cli_refuses_a_sealed_recipe_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--recipe", "held_out_a1", "--out", str(tmp_path)]) == 2
    assert "--final-score=held_out_a1" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_cli_flag_without_such_recipe_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["--recipe", "held_out_zz", "--final-score", "held_out_zz", "--out", str(tmp_path)])
        == 2
    )
    assert "unknown recipe" in capsys.readouterr().err
    assert main(["--recipe", "other", "--final-score", "held_out_zz", "--out", str(tmp_path)]) == 2
