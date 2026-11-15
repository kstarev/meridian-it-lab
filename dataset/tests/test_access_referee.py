"""The blind referee comparison: matching, safety scan, runner. Tuning world and fakes only."""

from dataclasses import asdict
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
    }
    assert rf.source_safety("import os")["disallowed_imports"] == 1
    assert rf.source_safety("from subprocess import run")["disallowed_imports"] == 1
    assert rf.source_safety("from . import x")["disallowed_imports"] == 1
    assert rf.source_safety('__import__("x")')["forbidden_names"] >= 1
    assert rf.source_safety("eval('1')")["forbidden_names"] >= 1
    assert rf.source_safety("import json\njson.os")["forbidden_names"] == 1


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
