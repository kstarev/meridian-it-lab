"""A full dry run on the tuning world, and the weekly report built from the run's files."""

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from meridian_dataset.oauth import (
    RADAR_DIR,
    REFERENCE_TIME,
    generate_oauth_world,
    write_oauth_world,
)
from meridian_oauth_radar.__main__ import main
from meridian_oauth_radar.report import read_run, weekly_report
from meridian_oauth_radar.run import LEDGER, run_files, write_run


@pytest.fixture
def world(tmp_path: Path) -> Path:
    """Only the radar's inputs, copied away from the answer key, so the run can't read it."""
    write_oauth_world(generate_oauth_world(), tmp_path / "w")
    shutil.copytree(tmp_path / "w" / RADAR_DIR, tmp_path / "radar")
    shutil.rmtree(tmp_path / "w")
    return tmp_path / "radar"


def digests(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def test_same_inputs_same_files_and_the_inputs_are_unchanged(world: Path) -> None:
    before = digests(world)
    assert run_files(world) == run_files(world)
    assert digests(world) == before


def test_the_report_is_rebuilt_from_the_run_files_alone(world: Path, tmp_path: Path) -> None:
    files = run_files(world)
    write_run(files, tmp_path / "run")
    assert weekly_report(read_run(tmp_path / "run")) == files["report.md"].decode()


def test_the_report_says_it_is_a_dry_run(world: Path) -> None:
    report = run_files(world)["report.md"].decode()
    assert report.startswith("# Shadow AI Radar: weekly report, 2026-11-16\n")
    assert "**A dry run:** nothing was removed and no message was sent." in report
    assert "Proposed (OAR-002), not yet accepted by the owner" in report
    assert "| Changes made / messages sent | 0 / 0 |" in report
    assert "## Emergency: compromised apps (a person must trigger this plan)" in report


def test_a_week_later_the_noticed_grants_are_in_the_plan(world: Path, tmp_path: Path) -> None:
    first = run_files(world)
    write_run(first, tmp_path / "day0")
    later = run_files(
        world,
        today=REFERENCE_TIME.date() + timedelta(days=7),
        ledger_path=tmp_path / "day0" / LEDGER,
    )
    noticed = {(n["user_key"], n["client_id"]) for n in json.loads(first[LEDGER])["notices"]}
    plan = json.loads(later["plans/after-notice-plan.json"])
    assert {(i["user_key"], i["client_id"]) for i in plan["items"]} == noticed
    assert (plan["status"], plan["applied"]) == ("proposed", False)
    assert json.loads(later["run.json"])["counts"]["notices_new"] == 0


def test_the_command_line_runs_a_world(
    world: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--world", str(world), "--out", str(tmp_path / "out")]) == 0
    assert "Nothing was removed or sent." in capsys.readouterr().out
    assert (tmp_path / "out" / "report.md").is_file()
    assert main(["--world", str(world)]) == 2
