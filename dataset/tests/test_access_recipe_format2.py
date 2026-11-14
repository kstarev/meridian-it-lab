"""Recipe format 2: new mess written as data, a custom HR case, and loud validation errors."""

import codecs
import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.access import AccessWorld, generate_access, write_access
from meridian_dataset.access import catalog as cat
from meridian_dataset.access.recipe import load_recipe_path, parse_recipe, tuning_seed

EXTRA_GIVEN = ["D'Arcy", "Zoë", "Ольга"]
EXTRA_FAMILY = ["O'Brien", "Smith-Jones", "Łukasiewicz", "Ковалёв", "Nguyễn"]
LAYOUT_E = {
    "name": "E",
    "delimiter": ";",
    "columns": [
        {"header": "Mail-Adresse", "field": "email"},
        {"header": "Benutzer", "field": "full_name"},
        {"header": "Abteilung", "field": "department"},
        {"header": "Letzter Login", "field": "last_login"},
        {"header": "Titel", "field": "title"},
    ],
    "preamble": ["Export by Test Suite", "Report: all users"],
    "footer": ["End of report"],
    "role_labels": {"member": "Mitglied", "admin": "Administrator"},
}


def _load(obj: dict[str, Any], tmp_path: Path) -> Any:
    path = tmp_path / "test_recipe.json"
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return load_recipe_path(path)


def _new_mess(obj: dict[str, Any]) -> dict[str, Any]:
    obj["name"] = "test_new_mess"
    obj["names"] = {
        "extra_given": EXTRA_GIVEN,
        "extra_family": EXTRA_FAMILY,
        "extra_share": 0.2,
        "display_styles": list(cat.DISPLAY_STYLES),
    }
    obj["username_patterns"] = ["{l}_{first}{n}", "{f}-{last}{n}"]
    obj["date_formats"] += [
        {"name": "excel", "special": "excel_serial"},
        {"name": "dotted", "strftime": "%d.%m.%y"},
        {"name": "epoch", "special": "unix_epoch"},
    ]
    obj["export_layouts"].append(LAYOUT_E)
    obj["exports"]["encodings"]["utf-16"] = 4
    obj["exports"]["noise"]["duplicate_row_share"] = 0.3
    return obj


@pytest.fixture(scope="module")
def mess_world(tmp_path_factory: pytest.TempPathFactory) -> tuple[AccessWorld, dict[str, Path]]:
    base = tmp_path_factory.mktemp("mess")
    path = base / "test_new_mess.json"
    source = Path(__file__).parents[1] / "src/meridian_dataset/access/recipes/tuning.json"
    path.write_text(
        json.dumps(_new_mess(json.loads(source.read_text(encoding="utf-8")))), encoding="utf-8"
    )
    recipe = load_recipe_path(path)
    world = generate_access(tuning_seed(recipe), recipe)
    return world, write_access(world, base / "out")


def _read(world: AccessWorld, files: dict[str, Path], app: str) -> tuple[Any, list[list[str]]]:
    export = next(e for e in world.exports if e.app_id == app)
    text = files[f"exports/{export.file_name}"].read_bytes().decode(export.encoding)
    return export, list(csv.reader(io.StringIO(text, newline=""), delimiter=export.delimiter))


def test_semicolon_layout_with_preamble_and_footer(
    mess_world: tuple[AccessWorld, dict[str, Path]],
) -> None:
    world, files = mess_world
    layout_e = [e for e in world.exports if e.layout == "E"]
    assert layout_e
    for export in layout_e:
        assert export.delimiter == ";" and export.header[0] == "Mail-Adresse"
        _, read = _read(world, files, export.app_id)
        assert read[0] == ["Export by Test Suite"] and read[1] == ["Report: all users"]
        assert read[2] == list(export.header)
        assert read[-1] == ["End of report"]
        assert [tuple(r) for r in read[3:-1]] == list(export.rows)


def test_new_date_formats_appear(mess_world: tuple[AccessWorld, dict[str, Path]]) -> None:
    world, _ = mess_world
    wanted = {"excel": r"4\d{4}", "dotted": r"\d\d\.\d\d\.\d\d", "epoch": r"1\d{9}"}
    for name, pattern in wanted.items():
        exports = [e for e in world.exports if e.date_format == name]
        assert exports, name
        cells = [
            cell
            for e in exports
            for row in e.rows
            for cell, col in zip(row, e.header, strict=True)
            if col in ("Last Login", "Created", "Added On", "Letzter Login") and cell
        ]
        assert cells and all(re.fullmatch(pattern, c) for c in cells), name


def test_new_username_pattern(mess_world: tuple[AccessWorld, dict[str, Path]]) -> None:
    world, _ = mess_world
    names = [row[0] for e in world.exports if e.layout == "D" for row in e.rows if any(row)]
    assert any(re.fullmatch(r"[a-z]_[a-z]+\d*", n) for n in names)
    assert any(re.fullmatch(r"[a-z]-[a-z]+\d*", n) for n in names)


def test_extra_names_and_ascii_emails(mess_world: tuple[AccessWorld, dict[str, Path]]) -> None:
    world, files = mess_world
    texts = []
    for e in world.exports:
        texts.append(files[f"exports/{e.file_name}"].read_bytes().decode(e.encoding))
    blob = "\n".join(texts)
    assert "'" in "".join(n for n in EXTRA_FAMILY + EXTRA_GIVEN if n in blob)
    assert any(n in blob for n in ("Smith-Jones", "Ковалёв", "Ольга", "Łukasiewicz", "Nguyễn"))
    assert re.search(r"[Ѐ-ӿ]", blob)  # a Cyrillic letter reaches a file
    names = {r.family_name for r in world.hris.records} | {r.given_name for r in world.hris.records}
    assert len(names & set(EXTRA_GIVEN + EXTRA_FAMILY)) >= 4
    for r in world.hris.records:
        assert r.work_email.isascii() and "'" not in r.work_email and " " not in r.work_email
    assert len({r.work_email for r in world.hris.records if r.status != "terminated"}) >= 200


def test_utf16_exports_and_duplicate_rows(
    mess_world: tuple[AccessWorld, dict[str, Path]],
) -> None:
    world, files = mess_world
    wide = [e for e in world.exports if e.encoding == "utf-16"]
    assert len(wide) == 4
    for e in wide:
        raw = files[f"exports/{e.file_name}"].read_bytes()
        assert raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    dupes = [
        1
        for e in world.exports
        for a, b in zip(e.rows, e.rows[1:], strict=False)
        if a == b and any(a)
    ]
    assert dupes
    assert len(world.exports) == 95


# ---- a custom case, end to end -----------------------------------------------------------

ROLE = cat.ROLE_BY_ID["eng-backend"]
NON_SSO_ONLY = next(a for a in cat.NON_SSO_IDS if a not in ROLE.other_apps)
SSO_EXTRA = next(a for a in cat.SSO_IDS[cat.BIRTHRIGHT_COUNT :] if a not in ROLE.sso_apps)


def _case() -> dict[str, Any]:
    return {
        "id": "test-converted-contractor",
        "description": "A contractor became an employee but kept a contractor-only app.",
        "people": [
            {
                "ref": "conv",
                "given_name": "Noor",
                "family_name": "Haddad",
                "role_id": "eng-backend",
                "employment_type": "employee",
                "country": "DE",
                "status": "active",
                "start_date": "2025-03-01",
                "current_worker": True,
                "standard_access": True,
            }
        ],
        "events": [
            {
                "day": 12,
                "ref": "conv",
                "kind": "field_change",
                "fields": {"employment_type": "employee"},
            }
        ],
        "accounts": [
            {"ref": "conv", "system": NON_SSO_ONLY, "last_login_day": 20},
            {"ref": None, "system": "slack", "account": "legacy-bot", "last_login_day": -1},
            {
                "ref": "conv",
                "system": SSO_EXTRA,
                "role": "admin",
                "granted_by_hand_day": 15,
            },
        ],
        "findings": [
            {
                "type": "mover_stale_access",
                "ref": "conv",
                "system": NON_SSO_ONLY,
                "expected_action": "remove",
                "day": 12,
                "detail": "Kept a contractor-only app.",
            },
            {
                "type": "orphaned_account",
                "ref": None,
                "account": "legacy-bot",
                "system": "slack",
                "expected_action": "remove",
                "day": 0,
            },
            {
                "type": "drift",
                "ref": "conv",
                "system": "okta",
                "app_id": SSO_EXTRA,
                "expected_action": "flag",
                "day": 15,
            },
        ],
    }


def test_custom_case_end_to_end(tuning_object: dict[str, Any], tmp_path: Path) -> None:
    tuning_object["name"] = "test_case"
    tuning_object["custom_cases"] = [_case()]
    recipe = _load(tuning_object, tmp_path)
    world = generate_access(tuning_seed(recipe), recipe)
    key = world.answer_key
    person = next(r for r in world.hris.records if r.given_name == "Noor")
    eid, email = person.employee_id, person.work_email
    assert person.department == "Engineering" and person.employment_type == "employee"
    assert len(key.true_roles) == 230 and dict(key.true_roles)[eid] == "eng-backend"
    current = [r for r in world.hris.records if r.status in ("active", "on_leave")]
    bulk = [e for e in world.hris.events if e.source == "bulk_import"]
    assert len(current) + len(bulk) == 230
    events = [e for e in world.hris.events if e.employee_id == eid]
    assert [(e.day, e.kind, e.fields, e.source) for e in events] == [
        (12, "field_change", (("employment_type", "employee"),), "hr")
    ]
    owners = {(o.system, o.account): o.owner_employee_id for o in key.owners}
    assert owners[("okta", email)] == eid and owners[("google", email)] == eid
    assert owners[("slack", "legacy-bot")] == ""
    assert any(s.user_name == "legacy-bot" and s.active for s in world.slack_users)
    rows = [
        (app, acct) for (app, acct), who in owners.items() if app == NON_SSO_ONLY and who == eid
    ]
    assert len(rows) == 1
    user = next(u for u in world.okta.users if u.login == email)
    okta_app = next(a.id for a in world.okta.apps if a.app_id == SSO_EXTRA)
    grant = next(a for a in world.okta.assignments if a.user_id == user.id and a.app_id == okta_app)
    assert grant.scope == "USER" and grant.role == "admin"
    assert any(
        e.target_user_id == user.id and e.target_app_id == okta_app and e.via == "admin console"
        for e in world.okta.log
    )
    found = {
        (f.type, f.system, f.app_id, f.account): f for f in key.findings if f.day in (0, 12, 15)
    }
    stale = found[("mover_stale_access", NON_SSO_ONLY, NON_SSO_ONLY, rows[0][1])]
    assert stale.employee_id == eid and stale.expected_action == "remove" and stale.day == 12
    assert found[("orphaned_account", "slack", "", "legacy-bot")].employee_id == ""
    assert found[("drift", "okta", SSO_EXTRA, email)].employee_id == eid


# ---- validation errors -----------------------------------------------------------------


def _expect(obj: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        generate_access(tuning_seed(parse_recipe(obj)), parse_recipe(obj))


def test_unknown_key_fails_loudly(tuning_object: dict[str, Any]) -> None:
    tuning_object["headcont"] = 230
    _expect(tuning_object, "unknown key.*headcont")


def test_nested_unknown_key_fails_loudly(tuning_object: dict[str, Any]) -> None:
    tuning_object["exports"]["noise"]["blanks"] = 1
    _expect(tuning_object, "unknown key.*blanks")


def test_unknown_placeholder(tuning_object: dict[str, Any]) -> None:
    tuning_object["username_patterns"] = ["{first}.{middle}"]
    _expect(tuning_object, "unknown placeholder")


def test_bad_codec(tuning_object: dict[str, Any]) -> None:
    tuning_object["exports"]["encodings"] = {"klingon": 2}
    _expect(tuning_object, "unknown encoding")


def test_codec_that_cannot_encode_enough_exports(tuning_object: dict[str, Any]) -> None:
    tuning_object["exports"]["encodings"] = {"ascii": 95}
    _expect(tuning_object, "ascii")


def test_layout_without_email_or_username(tuning_object: dict[str, Any]) -> None:
    tuning_object["export_layouts"][0]["columns"] = [{"header": "Name", "field": "full_name"}]
    _expect(tuning_object, "email or a username")


def test_unknown_field(tuning_object: dict[str, Any]) -> None:
    tuning_object["export_layouts"][0]["columns"].append({"header": "X", "field": "salary"})
    _expect(tuning_object, "unknown field")


def test_case_ref_missing(tuning_object: dict[str, Any]) -> None:
    case = _case()
    case["accounts"][0]["ref"] = "ghost"
    tuning_object["custom_cases"] = [case]
    _expect(tuning_object, "not a person in the case")


def test_finding_on_a_missing_account(tuning_object: dict[str, Any]) -> None:
    case = _case()
    case["findings"][1]["account"] = "nobody-here"
    tuning_object["custom_cases"] = [case]
    _expect(tuning_object, "does not exist")


def test_unused_layout_cannot_be_covered(tuning_object: dict[str, Any]) -> None:
    layouts = tuning_object["export_layouts"]
    for i in range(76):
        layouts.append({**layouts[0], "name": f"extra{i}"})
    _expect(tuning_object, "cannot all be used")


def test_bad_date_format(tuning_object: dict[str, Any]) -> None:
    tuning_object["date_formats"].append({"name": "x", "special": "julian"})
    _expect(tuning_object, "special must be one of")
    tuning_object["date_formats"][-1] = {"name": "y"}
    _expect(tuning_object, "exactly one of")


def test_format_must_be_two(tuning_object: dict[str, Any]) -> None:
    tuning_object["format"] = 1
    _expect(tuning_object, "format must be 2")


def test_gzip_recipe_loads_and_has_same_digest(
    tuning_object: dict[str, Any], tmp_path: Path
) -> None:
    import gzip

    from meridian_dataset.access.recipe import canonical_json

    path = tmp_path / "test_recipe.json.gz"
    path.write_bytes(gzip.compress(canonical_json(tuning_object), mtime=0))
    packed = load_recipe_path(path)
    plain = _load(tuning_object, tmp_path)
    assert packed.digest == plain.digest and len(packed.digest) == 64
