"""The leaver drill (milestone 5): a leaver's full deprovisioning plan, timed from the HRIS
change. Tuning world only; no network."""

import hashlib
import json

import pytest

from meridian_access import leaver_drill
from meridian_access.leaver_drill import LIMIT_SECONDS, RESULT_FILE, main, run_drill


def tree_hash(root):
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def drill(world_dir, policy, tmp_path_factory):
    before = tree_hash(world_dir)
    out = tmp_path_factory.mktemp("drill")
    result = run_drill(world_dir, out, policy)
    assert tree_hash(world_dir) == before  # the world given is never changed
    return result, out


def test_the_leaver_s_every_account_is_in_the_plan_in_under_five_minutes(drill):
    result, out = drill
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["accounts"] == len(result["covered"]) >= 3  # Okta, Google and Slack at least
    assert {k[0] for k in result["covered"]} >= {"okta", "google", "slack"}
    assert 0 < result["seconds"] < LIMIT_SECONDS == 300
    assert result["covered_before_the_change"] == 0  # the HRIS change is what put them there
    assert json.loads((out / RESULT_FILE).read_text(encoding="utf-8")) == result
    assert (out / "plan" / "plan.json").is_file()
    assert not (out / "world").exists()  # the copy is removed


def test_the_drill_s_plan_names_the_leaver_and_their_end_date(drill):
    result, out = drill
    plan = json.loads((out / "plan" / "plan.json").read_text(encoding="utf-8"))
    held = out / "plan" / "held" / "plan.json"
    rows = plan["changes"] + (json.loads(held.read_text())["changes"] if held.exists() else [])
    mine = [c for c in rows if c["employee_id"] == result["employee_id"]]
    assert mine and all(c["cause_date"] == result["end_date"] for c in mine)
    assert {c["type"] for c in mine} == {"leaver_access_left"}


def test_a_plan_that_leaves_an_account_out_fails_the_drill(
    world_dir, policy, tmp_path, monkeypatch
):
    real = leaver_drill.split_plan
    calls = []

    def drop_one(plan, pol):
        main_part, held = real(plan, pol)
        calls.append(1)
        if len(calls) == 2:  # the plan built after the HRIS change
            rows = main_part.data["changes"]
            gone = next(
                i
                for i, c in enumerate(rows)
                if c["system"] == "slack" and c["type"] == "leaver_access_left"
            )
            main_part.data["changes"] = rows[:gone] + rows[gone + 1 :]
        return main_part, held

    monkeypatch.setattr(leaver_drill, "split_plan", drop_one)
    result = run_drill(world_dir, tmp_path, policy)
    assert result["ok"] is False and len(result["missing"]) >= 1


def test_too_slow_fails_the_drill_and_the_command_exits_1(world_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(leaver_drill, "LIMIT_SECONDS", 0.0)
    assert main(["--world", str(world_dir), "--out", str(tmp_path)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert json.loads((tmp_path / RESULT_FILE).read_text(encoding="utf-8"))["ok"] is False


def test_the_command_exits_0_when_the_drill_passes(world_dir, tmp_path, capsys):
    assert main(["--world", str(world_dir), "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "accounts in the plan" in printed and printed.strip().endswith("ok")
