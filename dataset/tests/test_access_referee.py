"""The blind referee comparison: matching, safety scan, runner. Tuning world and fakes only."""

import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from meridian_dataset.access import AccessWorld
from meridian_dataset.access import referee as rf
from meridian_dataset.access.catalog import FINDING_TYPES
from meridian_dataset.access.model import AccessFinding

ROOT = Path(__file__).resolve().parents[2]


def _as_referee(findings: list[AccessFinding]) -> list[dict[str, str]]:
    keys = ("type", "employee_id", "account", "system", "app_id", "expected_action")
    return [{k: asdict(f)[k] for k in keys} for f in findings]


@pytest.fixture
def key(access_world: AccessWorld) -> list[AccessFinding]:
    return list(access_world.answer_key.findings)


def _non_hris(key: list[AccessFinding]) -> int:
    return next(i for i, f in enumerate(key) if f.system != "hris")


def test_key_against_itself_agrees(key: list[AccessFinding]) -> None:
    result = rf.compare(key, _as_referee(key))
    assert result.total_disagreements == 0
    assert list(result.counts) == list(FINDING_TYPES)
    assert all(r["agree"] == r["generator"] == r["referee"] for r in result.counts.values())
    assert sum(r["agree"] for r in result.counts.values()) == len(key)


def test_changed_action_differs(key: list[AccessFinding]) -> None:
    i = _non_hris(key)
    ref = _as_referee(key)
    ref[i]["expected_action"] = "flag" if key[i].expected_action != "flag" else "remove"
    result = rf.compare(key, ref)
    assert result.counts[key[i].type]["differ"] == 1
    assert [d["kind"] for d in result.disagreements] == ["differ"]
    assert result.disagreements[0]["id"] == "D001"


def test_dropped_and_extra_findings(key: list[AccessFinding]) -> None:
    i = _non_hris(key)
    ref = _as_referee(key)
    dropped = rf.compare(key, ref[:i] + ref[i + 1 :])
    assert dropped.counts[key[i].type]["generator_only"] == 1
    assert dropped.total_disagreements == 1
    extra = rf.compare(key[:i] + key[i + 1 :], ref)
    assert extra.counts[key[i].type]["referee_only"] == 1
    assert extra.total_disagreements == 1


def test_changed_type_differs_under_generator_type(key: list[AccessFinding]) -> None:
    i = _non_hris(key)
    other = next(t for t in FINDING_TYPES if t != key[i].type)
    ref = _as_referee(key)
    ref[i]["type"] = other
    result = rf.compare(key, ref)
    assert result.counts[key[i].type]["differ"] == 1
    assert result.counts[key[i].type]["referee"] == result.counts[key[i].type]["generator"] - 1
    assert result.counts[other]["referee"] == result.counts[other]["generator"] + 1


def test_account_case_and_whitespace_still_agree(key: list[AccessFinding]) -> None:
    i = _non_hris(key)
    ref = _as_referee(key)
    ref[i]["account"] = f"  {key[i].account.upper()} "
    ref[i]["system"] = f" {key[i].system.upper()}"
    assert rf.compare(key, ref).total_disagreements == 0


def test_hris_matches_on_employee_id_with_any_account() -> None:
    gen = AccessFinding("hr_data_error", "E1", "a", "hris", "", "flag", 3, "d")
    ref = [dict(_as_referee([gen])[0], account="something else", app_id="x")]
    assert rf.compare([gen], ref).total_disagreements == 0
    ref[0]["employee_id"] = "E2"
    assert rf.compare([gen], ref).total_disagreements == 2


def test_source_safety() -> None:
    assert rf.source_safety("import json\nfrom pathlib import Path\nx = 1") == {
        "disallowed_imports": 0,
        "forbidden_names": 0,
        "review_names": 0,
        "suspicious_strings": 0,
    }
    assert rf.source_safety("import os")["disallowed_imports"] == 1
    assert rf.source_safety("from subprocess import run")["disallowed_imports"] == 1
    assert rf.source_safety("from . import x")["disallowed_imports"] == 1
    assert rf.source_safety('__import__("x")')["forbidden_names"] >= 1
    assert rf.source_safety("eval('1')")["forbidden_names"] >= 1
    assert rf.source_safety("import json\njson.os")["forbidden_names"] == 1


@pytest.mark.parametrize(
    "snippet",
    [
        'import sys; sys.modules["os"].system("x")',
        'getattr(__builtins__, "ev"+"al")("1")',
        'open("/etc/passwd").read()',
        'pathlib.Path.home().joinpath("x").unlink()',
    ],
)
def test_source_safety_catches_the_bypass_snippets(snippet: str) -> None:
    assert any(n > 0 for n in rf.source_safety(snippet).values())


def test_review_names_are_counted_but_do_not_block(tmp_path: Path) -> None:
    source = (
        "import json, sys\n"
        "fields = getattr(json, 'dumps')\n"
        "open(sys.argv[2], 'w').write(fields({'findings': []}))\n"
    )
    safety = rf.source_safety(source)
    assert safety["review_names"] == 1
    assert all(safety[k] == 0 for k in rf.BLOCKING)
    world = tmp_path / "world"
    world.mkdir()
    assert rf.run_referee(source, world) == []


def test_source_safety_counts_suspicious_strings() -> None:
    assert rf.source_safety('x = "answer_key.json"')["suspicious_strings"] >= 1
    assert rf.source_safety('x = "hello"')["suspicious_strings"] == 0


_FAKE = """
import json, sys
from pathlib import Path
world, out = Path(sys.argv[1]), Path(sys.argv[2])
hris = json.loads((world / "hris.json").read_text())
{body}
"""
_GOOD = """
Path(out).write_text(json.dumps({"findings": [{"type": "hr_data_error", "employee_id": "E1",
    "account": "", "system": "hris", "app_id": "", "expected_action": "flag"}]}))
"""


def test_run_referee_valid(access_world: AccessWorld, tmp_path: Path) -> None:
    world = rf.raw_world(access_world, tmp_path / "w")
    assert len(rf.run_referee(_FAKE.format(body=_GOOD), world)) == 1


def test_run_referee_rejects_bad_output_and_exit(access_world: AccessWorld, tmp_path: Path) -> None:
    world = rf.raw_world(access_world, tmp_path / "w")
    bad = _GOOD.replace("hr_data_error", "made_up")
    with pytest.raises(ValueError, match="1 invalid findings of 1"):
        rf.run_referee(_FAKE.format(body=bad), world)
    with pytest.raises(RuntimeError, match="referee exited with 3"):
        rf.run_referee(_FAKE.format(body="sys.exit(3)"), world)


def test_run_referee_refuses_answer_key_and_unsafe_source(tmp_path: Path) -> None:
    (tmp_path / "answer_key.json").write_text("{}")
    with pytest.raises(ValueError, match="answer key"):
        rf.run_referee(_FAKE.format(body=_GOOD), tmp_path)
    marker = tmp_path / "ran"
    src = f"import os\nopen({str(marker)!r}, 'w')"
    (tmp_path / "answer_key.json").unlink()
    with pytest.raises(ValueError, match="disallowed_imports: 1"):
        rf.run_referee(src, tmp_path)
    assert not marker.exists()


def test_raw_world_drops_key_and_manifest(access_world: AccessWorld, tmp_path: Path) -> None:
    out = rf.raw_world(access_world, tmp_path / "w")
    names = {p.name for p in out.iterdir()}
    assert not names & {"answer_key.json", "manifest.json"}
    assert {"hris.json", "okta.json", "google.json", "slack_scim.json", "exports"} <= names
    assert list((out / "exports").glob("*.csv"))


def test_details_path_inside_repo_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the repo"):
        rf.check_details_path(ROOT / "docs" / "details.md")
    assert rf.check_details_path(tmp_path / "d.md") == (tmp_path / "d.md").resolve()


def test_cli_needs_final_score_flag() -> None:
    assert rf.main(["compare"]) == 2
    assert rf.main(["compare", "--final-score", "tuning"]) == 2


def test_table_total_row_is_column_sums(key: list[AccessFinding]) -> None:
    ref = _as_referee(key)[1:]
    counts = rf.compare(key, ref).counts
    lines = rf.table_markdown(counts).splitlines()
    assert len(lines) == 2 + len(FINDING_TYPES) + 1
    total = [int(c.strip(" *")) for c in lines[-1].split("|")[2:-1]]
    rows = [[int(c) for c in ln.split("|")[2:-1]] for ln in lines[2:-1]]
    assert total == [sum(col) for col in zip(*rows, strict=True)]


def test_log_file_has_table_block() -> None:
    log = ROOT / "docs" / "evals" / "2026-11-15-access-held-out-a1-referee.md"
    assert rf.read_table_block(log)


def test_split_marks_an_export_unreadable_when_its_owned_rows_are_called_orphaned(
    access_world: AccessWorld,
) -> None:
    from meridian_dataset.access.referee import compare, split_disagreements, unreadable_exports

    key = access_world.answer_key
    mine = _as_referee(list(key.findings))
    assert unreadable_exports(access_world, compare(key.findings, mine)) == set()
    app, _ = _export_with_owned_rows(access_world)  # asserts it found one
    rows = [o for o in key.owners if o.system == app and o.owner_employee_id]
    blind = [
        {
            "type": "orphaned_account",
            "employee_id": "",
            "account": o.account,
            "system": app,
            "app_id": app,
            "expected_action": "flag",
        }
        for o in rows
    ]
    result = compare(key.findings, mine + blind)
    assert unreadable_exports(access_world, result) == {app}
    counts, decided = split_disagreements(access_world, result)
    assert sum(r["unreadable"] for r in counts.values()) == result.total_disagreements
    assert decided == []


def test_run_referee_times_out(access_world: AccessWorld, tmp_path: Path) -> None:
    """A timeout raises subprocess.TimeoutExpired; subprocess.run has killed the child by then."""
    world = rf.raw_world(access_world, tmp_path / "w")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        rf.run_referee(_FAKE.format(body="while True:\n    pass"), world, timeout=0.5)
    assert time.monotonic() - started < 10


def test_run_referee_no_output_and_invalid_json(access_world: AccessWorld, tmp_path: Path) -> None:
    world = rf.raw_world(access_world, tmp_path / "w")
    with pytest.raises(ValueError, match="wrote no output"):
        rf.run_referee(_FAKE.format(body="pass"), world)
    with pytest.raises(ValueError, match="not valid JSON"):
        rf.run_referee(_FAKE.format(body="out.write_text('{nope')"), world)


def _blind(account: str, system: str) -> dict[str, str]:
    return {
        "type": "orphaned_account",
        "employee_id": "",
        "account": account,
        "system": system,
        "app_id": system,
        "expected_action": "flag",
    }


def _export_with_owned_rows(world: AccessWorld, even: bool = False) -> tuple[str, list[str]]:
    for e in world.exports:
        mine = [
            o.account
            for o in world.answer_key.owners
            if o.system == e.app_id and o.owner_employee_id
        ]
        if len(mine) >= 2 and (not even or len(mine) % 2 == 0):
            return e.app_id, mine
    raise AssertionError("no export with enough owned rows")


def test_exactly_half_wrongly_orphaned_is_not_unreadable_but_over_half_is(
    access_world: AccessWorld,
) -> None:
    key = access_world.answer_key
    app, accounts = _export_with_owned_rows(access_world, even=True)
    mine = _as_referee(list(key.findings))
    half = len(accounts) // 2
    at_half = rf.compare(key.findings, mine + [_blind(a, app) for a in accounts[:half]])
    assert rf.unreadable_exports(access_world, at_half) == set()
    over = rf.compare(key.findings, mine + [_blind(a, app) for a in accounts[: half + 1]])
    assert rf.unreadable_exports(access_world, over) == {app}


def test_a_wrong_orphan_in_okta_google_slack_never_makes_the_system_unreadable(
    access_world: AccessWorld,
) -> None:
    key = access_world.answer_key
    mine = _as_referee(list(key.findings))
    for system in ("okta", "google", "slack"):
        owned = [o.account for o in key.owners if o.system == system and o.owner_employee_id]
        assert owned
        blind = [_blind(a, system) for a in owned]
        result = rf.compare(key.findings, mine + blind)
        assert rf.unreadable_exports(access_world, result) == set()


def test_system_name_case_and_whitespace_still_found(access_world: AccessWorld) -> None:
    key = access_world.answer_key
    app, accounts = _export_with_owned_rows(access_world)
    mine = _as_referee(list(key.findings))
    blind = [_blind(f" {a.upper()} ", f" {app.upper()} ") for a in accounts]
    result = rf.compare(key.findings, mine + blind)
    assert rf.unreadable_exports(access_world, result) == {rf._norm(app)}
    counts, decided = rf.split_disagreements(access_world, result)
    assert sum(r["unreadable"] for r in counts.values()) == result.total_disagreements
    assert decided == []


def test_two_unmatched_findings_on_one_target_both_differ(key: list[AccessFinding]) -> None:
    i = _non_hris(key)
    f = key[i]
    twin = replace(
        f, expected_action="flag" if f.expected_action != "flag" else "remove", detail="a"
    )
    twin2 = replace(twin, detail="b")
    ref_row = _as_referee([f])[0]
    other = "remove" if ref_row["expected_action"] != "remove" else "flag"
    ref_row["expected_action"] = other
    ref_row["type"] = "drift" if f.type != "drift" else "break_glass"
    ref_row2 = dict(ref_row)
    result = rf.compare([twin, twin2], [ref_row, ref_row2])
    assert result.counts[f.type]["differ"] == 2
    assert [d["kind"] for d in result.disagreements] == ["differ", "differ"]
