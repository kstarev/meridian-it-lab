"""The plan on the tuning world: deterministic, scored against the key, within the time limit."""

import ast
import json
import time
from pathlib import Path

import pytest

from meridian_access import score
from meridian_access.plan import build_plan, main
from meridian_access.policy.load import default_policy_dir

DOC = Path(__file__).resolve().parents[2] / "docs" / "evals" / "2026-11-15-access-tuning-plan.md"
SRC = Path(__file__).resolve().parents[1] / "src" / "meridian_access"
PLANNER_FILES = (
    "diff.py",
    "plan.py",
    "summary.py",
    "slack_mock.py",
    "desired.py",
    "collect.py",
    "match.py",
    "mocks.py",
    "exports.py",
)


@pytest.fixture(scope="module")
def timed(world_dir, policy):
    started = time.perf_counter()
    plan = build_plan(world_dir, policy)
    return plan, time.perf_counter() - started


@pytest.fixture(scope="module")
def plan(timed):
    return timed[0]


def test_building_the_tuning_plan_takes_under_300_seconds_on_this_machine(timed):
    # a wall-clock budget, not a performance guarantee: it only fails if the build is very slow
    assert timed[1] < 300


def test_the_planner_modules_do_not_import_score_or_name_the_answer_key():
    for name in PLANNER_FILES:
        text = (SRC / name).read_text(encoding="utf-8")
        assert "answer_key" not in text, name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "meridian_access.score", name
                if node.module == "meridian_access":
                    assert all(a.name != "score" for a in node.names), name
            if isinstance(node, ast.Import):
                assert all(a.name != "meridian_access.score" for a in node.names), name


def test_building_a_plan_in_a_world_that_has_the_key_never_opens_it(
    keyed_world_dir, policy, monkeypatch, tmp_path
):
    """The key sits in this directory; a plan build and the CLI must not open it."""
    import builtins

    assert (keyed_world_dir / "answer_key.json").is_file()
    opened: list[str] = []
    real_open, real_read_text, real_read_bytes = (
        builtins.open,
        Path.read_text,
        Path.read_bytes,
    )

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_read_text(self, *args, **kwargs):
        opened.append(str(self))
        return real_read_text(self, *args, **kwargs)

    def spy_read_bytes(self, *args, **kwargs):
        opened.append(str(self))
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    build_plan(keyed_world_dir, policy)
    assert main(["--world", str(keyed_world_dir), "--out", str(tmp_path / "out")]) == 0
    assert opened  # the spies are in place
    assert not [p for p in opened if Path(p).name == "answer_key.json"]


def test_same_inputs_give_byte_identical_files(world_dir, policy, plan):
    again = build_plan(world_dir, policy)
    assert again.json() == plan.json()
    assert again.markdown() == plan.markdown()
    assert json.loads(plan.json()) == plan.data
    assert plan.json().endswith("}\n")


def test_plan_json_has_the_documented_fields(plan, policy):
    d = plan.data
    assert set(d) == {
        "version",
        "as_of",
        "actual_state_sha256",
        "desired_state_sha256",
        "policy_sha256",
        "changes",
        "held",
        "warnings",
        "suggested_map_entries",
        "counts",
        "approvals_required",
        "manual_tasks",
    }
    assert d["as_of"] == "2026-12-01"
    assert all(len(d[k]) == 64 for k in ("actual_state_sha256", "desired_state_sha256"))
    assert len(d["policy_sha256"]) == 64
    counts = d["counts"]
    assert counts["total"] == len(d["changes"]) == sum(counts["by_type"].values())
    assert counts["total"] == sum(counts["by_action"].values())
    assert counts["held"] == len(d["held"]) > 0
    over = policy.settings.approvals_over_n
    assert d["version"] == 2
    assert all("cause_date" in c for c in [*d["changes"], *d["held"]])
    assert d["approvals_required"] == over  # the bulk change alone asks for the larger number


def test_approvals_come_from_the_settings(world_dir, policy):
    import dataclasses

    custom = dataclasses.replace(
        policy,
        settings=dataclasses.replace(policy.settings, approvals_over_n=3, approvals_default=2),
    )
    assert build_plan(world_dir, custom).data["approvals_required"] == 3


def test_the_actual_state_hash_covers_the_okta_log_and_the_policy_hash_the_policy_used(
    world_dir, policy, tmp_path
):
    import dataclasses

    from meridian_access.collect import actual_state

    state = actual_state(world_dir, policy)
    base = build_plan(world_dir, policy, state=state).data["actual_state_sha256"]
    edited = dataclasses.replace(state.log[0], event_type="user.session.start")
    other = dataclasses.replace(state, log=(edited, *state.log[1:]))
    assert build_plan(world_dir, policy, state=other).data["actual_state_sha256"] != base
    copy = tmp_path / "policy"
    copy.mkdir()
    for f in default_policy_dir().glob("*.yaml"):
        (copy / f.name).write_bytes(f.read_bytes())
    (copy / "break_glass.yaml").write_text(
        (copy / "break_glass.yaml").read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    same = build_plan(world_dir, policy, state=state)
    used = build_plan(world_dir, policy, state=state, policy_dir=copy)
    assert used.data["policy_sha256"] != same.data["policy_sha256"]


def test_the_policy_hash_changes_when_a_policy_file_changes(tmp_path):
    from meridian_access.plan import policy_sha256

    copy = tmp_path / "policy"
    copy.mkdir()
    for f in default_policy_dir().glob("*.yaml"):
        (copy / f.name).write_bytes(f.read_bytes())
    before = policy_sha256(copy)
    assert before == policy_sha256(default_policy_dir())
    (copy / "settings.yaml").write_text(
        (copy / "settings.yaml").read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    assert policy_sha256(copy) != before


def test_every_flag_on_an_export_row_is_a_manual_task(plan):
    flags = [
        c
        for c in plan.changes
        if c["action"] == "flag" and c["system"] not in ("okta", "google", "slack", "hris")
    ]
    assert len(plan.data["manual_tasks"]) == len(flags) > 0
    task = plan.data["manual_tasks"][0]
    assert task["task"].startswith("Remove ") and task["task"].endswith(", confirm in next export")


def test_nothing_is_changed_for_a_held_person_or_a_break_glass_account(plan):
    held_people = {c["employee_id"] for c in plan.data["held"]}
    assert held_people and "" not in held_people
    assert not [c for c in plan.changes if c["employee_id"] in held_people]
    for c in plan.changes:
        if c["account"].lower().startswith("breakglass-"):
            assert c["type"] == "break_glass" and c["action"] == "never_touch"
    assert [c["type"] for c in plan.changes if c["action"] == "block_two_approvers"] == [
        "hris_mass_termination"
    ]


def test_plan_md_has_counts_sections_and_plain_english_lines(plan):
    md = plan.markdown()
    assert md.startswith("# Access plan for 2026-12-01\n")
    for heading in ("## Warnings", "## Held", "## Manual tasks", "## Movers"):
        assert heading in md
    lines = [ln for ln in md.splitlines() if ln.startswith("- Remove ") and " from " in ln]
    assert any("moved to" in ln for ln in lines)


def test_ambiguous_and_unreviewed_accounts_are_warnings_not_changes(plan):
    ambiguous = [w for w in plan.data["warnings"] if "ambiguous owner" in w]
    assert ambiguous
    for w in ambiguous:
        who = w.split(" (")[0]
        assert not [c for c in plan.changes if c["account"] == who]


def test_scores_per_type_on_the_tuning_world(world, keyed_world_dir, plan):
    scores = score.score_plan(plan, score.load_key(keyed_world_dir))
    for type_, s in scores.items():
        print(type_, s.precision.cell(), s.recall.cell())
        if type_ == "hr_data_error":
            assert s.predicted == s.expected == 0
            continue
        assert s.precision.value >= 0.90, type_
        assert s.recall.value >= 0.90, type_


def test_score_plan_counts_each_kind_of_outcome():
    key = {
        "findings": [
            {
                "type": "drift",
                "employee_id": "E1",
                "account": "A@x",
                "system": "okta",
                "app_id": "app",
                "expected_action": "remove",
            },
            {
                "type": "hris_mass_termination",
                "employee_id": "",
                "account": "",
                "system": "hris",
                "app_id": "",
                "expected_action": "block_two_approvers",
            },
            {
                "type": "drift",
                "employee_id": "E2",
                "account": "b@x",
                "system": "okta",
                "app_id": "app",
                "expected_action": "flag",
            },
        ]
    }

    def change(type_, eid, account, system, app, action):
        return {
            "type": type_,
            "employee_id": eid,
            "account": account,
            "system": system,
            "app_id": app,
            "action": action,
        }

    plan = {
        "changes": [
            change("drift", "E1", " a@X ", "okta", "app", "remove"),  # case and spaces ignored
            change("drift", "E2", "b@x", "okta", "app", "remove"),  # wrong action
            change("drift", "E3", "c@x", "okta", "app", "remove"),  # not in the key
            change("hris_mass_termination", "", "", "hris", "", "block_two_approvers"),
        ]
    }
    scores = score.score_plan(plan, key)
    assert (scores["drift"].precision.successes, scores["drift"].predicted) == (1, 3)
    assert (scores["drift"].recall.successes, scores["drift"].expected) == (1, 2)
    assert scores["hris_mass_termination"].precision.value == 1.0
    assert scores["all"].predicted == 4


def test_the_results_doc_is_the_generated_table(keyed_world_dir):
    doc = DOC.read_text(encoding="utf-8")
    expected = score.render_plan_score(score.score_plan_world(keyed_world_dir))
    assert score.read_plan_block(doc) == expected, (
        "refresh with `uv run --locked python -m meridian_access.score plan "
        "--world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-tuning-plan.md`"
    )


def test_replace_plan_block_needs_its_markers():
    with pytest.raises(ValueError):
        score.replace_plan_block("no markers", "x")
    doc = f"a\n{score.PLAN_START}\nold\n{score.PLAN_END}\nb"
    assert score.read_plan_block(score.replace_plan_block(doc, "new\n")) == "new\n"


def test_the_cli_writes_every_file_and_the_timing_is_separate(world_dir, tmp_path, capsys):
    out = tmp_path / "plan"
    assert main(["--world", str(world_dir), "--out", str(out)]) == 0
    for name in ("plan.json", "PLAN.md", "summary.md", "slack-message.json", "timing.json"):
        assert (out / name).is_file(), name
    timing = json.loads((out / "timing.json").read_text(encoding="utf-8"))
    assert 0 < timing["elapsed_seconds"] < 300
    assert "elapsed_seconds" not in (out / "plan.json").read_text(encoding="utf-8")
    assert "template" in (out / "summary.md").read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    assert "changes:" in printed and "elapsed:" in printed
    first = (out / "plan.json").read_bytes()
    main(["--world", str(world_dir), "--out", str(tmp_path / "again")])
    assert (tmp_path / "again" / "plan.json").read_bytes() == first


def test_a_failure_while_writing_leaves_no_partial_files(world_dir, tmp_path, monkeypatch):
    from meridian_access import summary

    def boom(plan, client=None):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(summary, "summary_markdown", boom)
    out = tmp_path / "plan"
    with pytest.raises(RuntimeError):
        main(["--world", str(world_dir), "--out", str(out)])
    assert list(out.iterdir()) == []  # nothing half-written, no temporary directory left
