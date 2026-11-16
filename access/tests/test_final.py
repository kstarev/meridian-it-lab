"""Milestone 7 prep: the frozen prompts, the final run's guards, the dry check and the workflow.

Fake clients only: no API call, no key, $0 spent. The sealed world is never built here: every
test that reaches the point where the final run would build it replaces that step with the
tuning world, or checks that it was never reached.
"""

import dataclasses
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from meridian_access import summary
from meridian_access.ai import final, frozen, matching, pricing, titles
from meridian_access.ai.__main__ import main as ai_main
from meridian_access.ai.budget import load_budget
from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.pricing import HAIKU, SONNET
from meridian_access.ai.spend import ITEM_ENV, SpendCapReached, SpendLedger
from meridian_access.match import Match, MatchResult
from meridian_access.score import (
    matching_disagreements,
    plan_disagreements,
    score_plan,
    title_disagreements,
)
from meridian_dataset.access.model import AccountOwner

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "access-ai-final.yml"


class FakeSDK:
    """Stands in for anthropic.Anthropic: records every request, answers with the first
    candidate (or the first role), never touches a network."""

    def __init__(self):
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.requests.append(params)
        answer = {}
        for name, prop in params["output_config"]["format"]["schema"]["properties"].items():
            if prop["type"] == "array":
                answer[name] = []
            elif "enum" in prop:
                answer[name] = prop["enum"][0]
            else:
                answer[name] = "A short plan with {total} changes."
        return SimpleNamespace(
            model=params["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=900, output_tokens=60),
            content=[SimpleNamespace(type="text", text=json.dumps(answer))],
        )


def _explode(*args, **kwargs):
    raise AssertionError("the sealed world, the SDK or a paid call was reached")


@pytest.fixture
def never_unsealed(monkeypatch):
    """Fail if anything tries to build the sealed world or a real client."""
    import meridian_dataset.access.recipe as recipe

    monkeypatch.setattr(final, "build_sealed_world", _explode)
    monkeypatch.setattr(recipe, "load_recipe", _explode)
    monkeypatch.setattr(ClaudeClient, "_sdk_client", _explode)


def _test_ledger(tmp_path, run_limit=2.0):
    return SpendLedger(tmp_path / "ledger.jsonl", cap_usd=3.0, run_limit_usd=run_limit)


def _frozen_client(sdk, ledger):
    return frozen.FrozenClaudeClient(frozen.FROZEN_MODEL, client=sdk, ledger=ledger)


# ------------------------------------------------------------------------------- freeze


def test_the_prompts_are_frozen_by_hash():
    assert frozen.problems() == []
    names = {f"{kind}.{part}" for kind in frozen.KINDS for part in frozen.PARTS}
    assert set(frozen.PINNED_SHA256) == names
    assert frozen.current_hashes() == frozen.PINNED_SHA256
    assert frozen.freeze_digest(frozen.PINNED_SHA256) == frozen.FREEZE_DIGEST
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in frozen.PINNED_SHA256.values())


def test_a_system_prompt_hash_is_the_sha256_of_its_exact_text():
    """So anyone can check a pin by hand: the bytes of the text, nothing added."""
    for kind, text in (
        ("title", titles.SYSTEM),
        ("owner", matching.SYSTEM),
        ("summary", summary.SYSTEM),
    ):
        assert hashlib.sha256(text.encode()).hexdigest() == frozen.PINNED_SHA256[f"{kind}.system"]


def test_the_frozen_model_is_the_chosen_one():
    assert frozen.FROZEN_MODEL == pricing.CHOSEN_MODEL == HAIKU


@pytest.mark.parametrize(
    ("module", "name", "value", "changed"),
    [
        (titles, "SYSTEM", titles.SYSTEM + " ", "title.system"),
        (matching, "SYSTEM", matching.SYSTEM.replace("none", "nobody"), "owner.system"),
        (summary, "SYSTEM", summary.SYSTEM[:-1], "summary.system"),
        (summary, "MAX_TOKENS", 600, "summary.request"),
        (summary, "SCHEMA", {**summary.SCHEMA, "additionalProperties": True}, "summary.schema"),
        (summary, "TYPE_WORDS", {**summary.TYPE_WORDS, "drift": "drifts"}, "summary.user"),
        (matching, "EVIDENCE_FIELDS", (*matching.EVIDENCE_FIELDS, "email"), "owner.schema"),
        (titles, "title_prompt", lambda t, d, tm, r: f"Title: {t}\n{d}\n{tm}", "title.user"),
        (matching, "candidate_payload", lambda r: {"id": r.employee_id}, "owner.user"),
    ],
)
def test_any_change_to_what_is_sent_breaks_the_freeze(monkeypatch, module, name, value, changed):
    monkeypatch.setattr(module, name, value)
    issues = frozen.problems()
    assert f"{changed} differs from its pinned hash" in issues
    with pytest.raises(frozen.PromptsNotFrozen):
        frozen.require_frozen()


def test_turning_thinking_on_breaks_the_freeze(monkeypatch):
    from meridian_access.ai import client

    monkeypatch.setattr(client, "THINKING_OFF_MODELS", frozenset())
    issues = frozen.problems()
    assert {f"{k}.request differs from its pinned hash" for k in frozen.KINDS} <= set(issues)


def test_another_chosen_model_breaks_the_freeze_and_the_frozen_client(monkeypatch):
    monkeypatch.setattr(pricing, "CHOSEN_MODEL", SONNET)
    assert any("chosen model" in issue for issue in frozen.problems())
    with pytest.raises(frozen.PromptsNotFrozen):
        frozen.FrozenClaudeClient(frozen.FROZEN_MODEL, client=FakeSDK())
    monkeypatch.setattr(pricing, "CHOSEN_MODEL", HAIKU)
    with pytest.raises(frozen.PromptsNotFrozen):
        frozen.FrozenClaudeClient(SONNET, client=FakeSDK())


def test_the_frozen_client_sends_only_pinned_prompts_and_limits(tmp_path):
    sdk, ledger = FakeSDK(), _test_ledger(tmp_path)
    client = _frozen_client(sdk, ledger)
    with pytest.raises(frozen.PromptsNotFrozen):
        client.complete_json("You are a different prompt.", "hi", summary.SCHEMA)
    with pytest.raises(frozen.PromptsNotFrozen):  # a pinned prompt with another token limit
        client.complete_json(summary.SYSTEM, "hi", summary.SCHEMA, max_tokens=400)
    assert sdk.requests == [] and not ledger.path.exists()  # refused before the spend check
    s = titles.suggest_role("Probe Title", "Probe Department", "Probe Team", ["a"], client)
    assert s is not None and len(sdk.requests) == 1


# ------------------------------------------------------------------- the final command


def _env(**extra):
    return {"ACCESS_AI_LIVE": "1", **extra}


@pytest.mark.parametrize(
    "flag", ["", "tuning", "held_out_a2", "held_out_a1 ", "HELD_OUT_A1", "held-out-a1"]
)
def test_the_final_command_refuses_without_the_final_score_flag(
    never_unsealed, tmp_path, capsys, flag
):
    argv = ["final", "--out", str(tmp_path / "out")]
    argv += ["--final-score", flag] if flag else []
    assert ai_main(argv) == 2
    assert "refusing" in capsys.readouterr().out
    assert final.final_main(flag, tmp_path, k=1, summaries=True, env=_env()) == 2


def test_the_final_score_flag_is_the_one_the_dataset_unseals_by():
    from meridian_dataset.access.recipe import available_recipes

    assert final.FINAL_SCORE == "held_out_a1"
    assert final.FINAL_SCORE in available_recipes()  # a listing only: nothing is opened


def test_the_final_command_refuses_when_a_prompt_changed(
    never_unsealed, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(titles, "SYSTEM", titles.SYSTEM + " Be brief.")
    assert final.final_main("held_out_a1", tmp_path, k=1, summaries=True, env=_env()) == 2
    out = capsys.readouterr().out
    assert "not the frozen ones" in out and "title.system" in out
    assert "Be brief" not in out  # names only, never prompt text


def test_the_final_command_refuses_another_model(never_unsealed, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pricing, "CHOSEN_MODEL", SONNET)
    assert final.final_main("held_out_a1", tmp_path, k=1, summaries=True, env=_env()) == 2
    assert "chosen model" in capsys.readouterr().out


def test_the_final_command_refuses_without_the_live_switch(never_unsealed, tmp_path):
    assert final.final_main("held_out_a1", tmp_path, k=1, summaries=True, env={}) == 2


def test_the_final_command_refuses_another_budget_item(never_unsealed, tmp_path, capsys):
    env = _env(**{ITEM_ENV: "aac-model-comparison"})  # a dict: the real environment is untouched
    assert final.final_main("held_out_a1", tmp_path, k=1, summaries=True, env=env) == 2
    assert "charged to aac-m7-final-score" in capsys.readouterr().out


def test_the_final_command_refuses_an_out_dir_in_the_repo(never_unsealed, capsys):
    for out in (None, ROOT / "out" / "access-final", ROOT / "docs" / "evals"):
        assert final.final_main("held_out_a1", out, k=1, summaries=True, env=_env()) == 2
    assert "outside the repository" in capsys.readouterr().out


def _budget_with(approved: bool):
    b = load_budget()
    items = dict(b.items)
    items[final.BUDGET_ITEM] = dataclasses.replace(items[final.BUDGET_ITEM], approved=approved)
    return dataclasses.replace(b, items=items)


def _ledgers(monkeypatch, tmp_path, approved, when=None):
    """The final command's ledger, on a copy of the budget with the item's approval set."""
    made = []

    def factory(item):
        ledger = SpendLedger(
            tmp_path / "ledger.jsonl",
            item=item,
            budget=_budget_with(approved),
            clock=lambda: when or datetime(2026, 11, 20, 12, tzinfo=UTC),  # inside the window
            run_limit_usd=2.0,
        )
        made.append(ledger)
        return ledger

    monkeypatch.setattr(final, "SpendLedger", factory)
    return made


def test_the_final_command_refuses_until_the_owner_approves_the_item(
    never_unsealed, monkeypatch, tmp_path, capsys
):
    made = _ledgers(monkeypatch, tmp_path, approved=False)
    assert final.final_main("held_out_a1", tmp_path / "o", k=1, summaries=True, env=_env()) == 2
    assert "not approved" in capsys.readouterr().out
    assert made[0].item == final.BUDGET_ITEM


def test_the_final_command_refuses_after_the_paid_window(
    never_unsealed, monkeypatch, tmp_path, capsys
):
    _ledgers(monkeypatch, tmp_path, approved=True, when=datetime(2026, 12, 18, 0, 1, tzinfo=UTC))
    assert final.final_main("held_out_a1", tmp_path / "o", k=1, summaries=True, env=_env()) == 2
    assert "paid window closed" in capsys.readouterr().out


def test_the_final_score_item_belongs_to_access():
    item = load_budget().items[final.BUDGET_ITEM]
    assert item.project == "access" and item.estimate_usd <= item.cap_usd


def test_the_final_command_end_to_end_on_a_stand_in_world(
    keyed_world_dir, monkeypatch, tmp_path, capsys
):
    """Every guard passed (on a copy of the budget with the item approved), and the tuning
    world standing in for the sealed one: the calls go to a fake SDK and are charged to the
    final-score item."""
    made = _ledgers(monkeypatch, tmp_path, approved=True)
    sdk = FakeSDK()

    def stand_in(name, target):
        assert name == "held_out_a1"
        shutil.copytree(keyed_world_dir, target)
        return target

    monkeypatch.setattr(final, "build_sealed_world", stand_in)
    monkeypatch.setattr(
        ClaudeClient, "_sdk_client", lambda self: self._client if self._client else sdk
    )
    out = tmp_path / "final-out"
    assert final.final_main("held_out_a1", out, k=1, summaries=True, env=_env()) == 0
    printed = capsys.readouterr().out
    assert "done: 93 calls of 93 planned" in printed and "precision" not in printed
    entries = [json.loads(line) for line in made[0].path.read_text().splitlines()]
    assert len(entries) == len(sdk.requests) == 93
    assert {e["budget_item"] for e in entries} == {final.BUDGET_ITEM}
    assert {e["model"] for e in entries} == {frozen.FROZEN_MODEL}
    assert sorted(p.name for p in out.iterdir()) == [
        "details.json",
        "report.json",
        "rulings-template.md",
    ]


def test_a_failure_after_the_start_never_prints_its_message(monkeypatch, tmp_path, capsys):
    _ledgers(monkeypatch, tmp_path, approved=True)

    def leaky(name, target):
        raise KeyError("probe.person9@meridian.example")

    monkeypatch.setattr(final, "build_sealed_world", leaky)
    assert final.final_main("held_out_a1", tmp_path / "o", k=1, summaries=True, env=_env()) == 1
    out = capsys.readouterr().out
    assert "KeyError" in out and "probe.person9" not in out


# ---------------------------------------------------------------------- the dry check


def test_the_dry_check_on_the_tuning_world(world_dir, monkeypatch, capsys):
    monkeypatch.setattr(ClaudeClient, "_sdk_client", lambda self: self._client or _explode())
    assert ai_main(["final-check", "--world", str(world_dir), "--k", "3"]) == 0
    out = capsys.readouterr().out
    assert "no calls made" in out and "Tuning world only" in out
    assert "calls per repeat: 93 (76 title, 15 owner, 2 summary); k=3: 279" in out
    for name, digest in frozen.PINNED_SHA256.items():
        assert f"{name}" in out and digest in out
    assert frozen.FREEZE_DIGEST in out and "frozen: yes" in out
    check = final.dry_check(world_dir, k=3)
    assert check["calls_made"] == 0 and check["problems"] == []
    assert (
        check["calls"] == 3 * check["calls_per_repeat"] == 3 * sum(check["calls_by_kind"].values())
    )
    # the upper bound at Haiku's rates: under a cent per call, and above the measured cost
    # of the comparison (k=1, 91 calls: $0.0109)
    assert 0.0109 < check["worst_case_usd_per_repeat"] < 0.01 * check["calls_per_repeat"]
    per_repeat = check["worst_case_usd_per_repeat"]
    assert check["worst_case_usd"] == pytest.approx(3 * per_repeat, abs=1e-5)


def test_the_dry_check_says_so_when_the_prompts_are_not_frozen(world_dir, monkeypatch, capsys):
    monkeypatch.setattr(summary, "SYSTEM", summary.SYSTEM + "!")
    assert ai_main(["final-check", "--world", str(world_dir)]) == 1
    assert "NOT FROZEN" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["seed", "recipe"])
def test_the_dry_check_refuses_any_world_but_the_tuning_world(world_dir, tmp_path, change, capsys):
    other = tmp_path / "world"
    shutil.copytree(world_dir, other)
    name, key, value = {
        "seed": ("hris.json", "seed", 4242),
        "recipe": ("manifest.json", "recipe_name", "held_out_a1"),
    }[change]
    data = json.loads((other / name).read_text())
    data[key] = value
    (other / name).write_text(json.dumps(data))
    assert ai_main(["final-check", "--world", str(other)]) == 2
    assert "tuning world only" in capsys.readouterr().out


def test_the_dry_check_builds_exactly_what_the_run_sends(
    keyed_world_dir, world_dir, policy, tmp_path
):
    _, rehearsed = final.rehearse(final.build_inputs(world_dir, policy))
    sdk, ledger = FakeSDK(), _test_ledger(tmp_path)
    final.run_world(keyed_world_dir, tmp_path / "o", _frozen_client(sdk, ledger), ledger)
    assert sdk.requests == rehearsed
    assert {r["model"] for r in rehearsed} == {frozen.FROZEN_MODEL}
    assert all(r["thinking"] == {"type": "disabled"} for r in rehearsed)
    kinds = [frozen.kind_of(r["system"]) for r in rehearsed]
    assert None not in kinds and kinds.count("summary") == 2


# ------------------------------------------------------------------------- the run


def test_the_run_end_to_end_with_a_fake_client(keyed_world_dir, tmp_path):
    sdk, ledger = FakeSDK(), _test_ledger(tmp_path)
    out = tmp_path / "final"
    report = final.run_world(keyed_world_dir, out, _frozen_client(sdk, ledger), ledger, k=2)
    assert report["status"].startswith("unruled")
    assert report["world"] == "tuning" and report["model"] == frozen.FROZEN_MODEL
    assert report["calls_made"] == report["calls_planned"] == 2 * 93 == len(sdk.requests)
    assert report["freeze_digest"] == frozen.FREEZE_DIGEST
    assert 0 < report["cost_usd"] <= report["worst_case_usd"]
    scores = report["scores"]
    assert [r["repeat"] for r in scores["repeats"]] == ["repeat 1", "repeat 2"]
    for r in scores["repeats"]:
        assert r["titles"]["total"] == 76 and r["no_answer"] == 0 and not r["stopped"]
        assert set(r["summaries_passed"]) == {"main", "held"}
        assert r["ai_matches"]["made"] == r["ai_matches"]["right"] + r["ai_matches"]["false"]
    details = json.loads((out / "details.json").read_text())
    assert report["disagreements"] == {k: len(v) for k, v in details.items()}
    template = (out / "rulings-template.md").read_text()
    rows = [line for line in template.splitlines() if re.match(r"\| [FMT]\d{3} \|", line)]
    assert len(rows) == sum(report["disagreements"].values())


def test_the_run_refuses_before_any_call_when_the_worst_case_does_not_fit(
    keyed_world_dir, tmp_path
):
    sdk, ledger = FakeSDK(), _test_ledger(tmp_path, run_limit=0.01)
    out = tmp_path / "final"
    with pytest.raises(SpendCapReached):
        final.run_world(keyed_world_dir, out, _frozen_client(sdk, ledger), ledger, k=3)
    assert sdk.requests == [] and not ledger.path.exists() and not out.exists()


def test_the_run_takes_only_the_frozen_client_and_an_out_dir_outside_the_repo(
    keyed_world_dir, tmp_path
):
    sdk, ledger = FakeSDK(), _test_ledger(tmp_path)
    with pytest.raises(frozen.PromptsNotFrozen):
        final.run_world(keyed_world_dir, tmp_path, ClaudeClient(HAIKU, sdk, ledger), ledger)
    with pytest.raises(ValueError, match="outside the repository"):
        final.run_world(keyed_world_dir, ROOT / "out" / "x", _frozen_client(sdk, ledger), ledger)
    with pytest.raises(ValueError, match="k must be"):
        final.run_world(keyed_world_dir, tmp_path, _frozen_client(sdk, ledger), ledger, k=4)
    assert sdk.requests == []


def test_the_held_out_world_is_built_only_by_its_name(tmp_path):
    with pytest.raises(ValueError, match="final-score=held_out_a1"):
        final.build_sealed_world("tuning", tmp_path / "w")
    assert not (tmp_path / "w").exists()


# ------------------------------------------------------------- disagreements (AAC-010)


def _change(type_, account, action, employee="E1", system="okta", app_id="app"):
    return {
        "type": type_,
        "employee_id": employee,
        "account": account,
        "system": system,
        "app_id": app_id,
        "action": action,
    }


def _finding(type_, account, action, employee="E1", system="okta", app_id="app"):
    f = _change(type_, account, action, employee, system, app_id)
    f["expected_action"] = f.pop("action")
    return f


def test_plan_disagreements_name_the_three_kinds_and_cost_what_the_score_counts():
    plan = {
        "changes": [
            _change("drift", "a@x", "revert"),  # agrees
            _change("drift", "b@x", "revert"),  # extra
            _change("orphaned_account", "c@x", "remove"),  # differs from the key's mover
        ]
    }
    key = {
        "findings": [
            _finding("drift", "a@x", "revert"),
            _finding("leaver_access_left", "d@x", "remove"),  # missed
            _finding("mover_stale_access", "c@x", "remove"),
        ]
    }
    found = plan_disagreements(plan, key)
    assert [(d["id"], d["kind"]) for d in found] == [
        ("F001", "missed"),
        ("F002", "extra"),
        ("F003", "differ"),
    ]
    assert found[2]["tool"]["type"] == "orphaned_account"
    assert found[2]["key"]["type"] == "mover_stale_access"


def test_plan_disagreements_match_the_plan_score_on_the_tuning_world(keyed_world_dir, policy):
    from meridian_access.plan import build_plan
    from meridian_access.score import load_key

    key = load_key(keyed_world_dir)
    plan = build_plan(keyed_world_dir, policy)
    scores = score_plan(plan, key)["all"]
    found = plan_disagreements(plan, key)
    kinds = [d["kind"] for d in found]
    false_pos = scores.predicted - scores.precision.successes
    false_neg = scores.expected - scores.recall.successes
    assert false_pos == kinds.count("extra") + kinds.count("differ")
    assert false_neg == kinds.count("missed") + kinds.count("differ")


def test_matching_disagreements_list_every_account_any_result_gets_wrong():
    owners = [
        AccountOwner("google", "a@x", "E1"),  # right everywhere
        AccountOwner("google", "b@x", "E2"),  # left unmatched by the rules, right with AI
        AccountOwner("google", "c@x", ""),  # an orphan the AI gives a person
    ]
    rules = MatchResult({("google", "", "a@x"): Match("E1", "email", 1.0)}, [], [])
    with_ai = MatchResult(
        {
            **rules.matches,
            ("google", "", "b@x"): Match("E2", "ai", 0.5),
            ("google", "", "c@x"): Match("E9", "ai", 0.5),
        },
        [],
        [],
    )
    found = matching_disagreements({"rules only": rules, "rules + AI": with_ai}, owners)
    assert [(d["id"], d["account"]) for d in found] == [("M001", "b@x"), ("M002", "c@x")]
    assert found[0]["kind"] == {"rules only": "left unmatched", "rules + AI": "agree"}
    assert found[1]["kind"] == {"rules only": "agree", "rules + AI": "false match"}


def test_title_disagreements_compare_with_the_majority_role():
    people = [
        SimpleNamespace(employee_id="E1", job_title="Sr AE"),
        SimpleNamespace(employee_id="E2", job_title="Sr AE"),
        SimpleNamespace(employee_id="E3", job_title="Dev"),
    ]
    roles = [("E1", "sales-ae"), ("E2", "sales-ae"), ("E3", "engineer")]
    answers = {"repeat 1": {"Sr AE": "sales-ae", "Dev": "none"}, "repeat 2": {"Sr AE": "x"}}
    found = title_disagreements(answers, people, roles)
    assert [(d["id"], d["title"], d["key_role"]) for d in found] == [
        ("T001", "Dev", "engineer"),
        ("T002", "Sr AE", "sales-ae"),
    ]
    assert found[0]["tool"] == {"repeat 1": "none", "repeat 2": "none"}


# ------------------------------------------------------------------------- the workflow


def _workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data, data.get("on", data.get(True))  # YAML reads a bare `on` as True


def test_the_final_workflow_is_manual_approved_and_least_privilege():
    data, triggers = _workflow()
    assert set(triggers) == {"workflow_dispatch"}
    assert triggers["workflow_dispatch"]["inputs"]["k"]["options"] == ["3", "1"]
    assert data["permissions"] == {}
    (job,) = data["jobs"].values()
    assert job["environment"] == "access-ai"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["if"] == "github.ref == 'refs/heads/main'"


def test_the_final_workflow_pins_every_action_to_a_sha_the_repo_already_uses():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    known = (ROOT / ".github" / "workflows" / "access-ai-compare.yml").read_text()
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), u
        assert u in known, u
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False


def test_the_final_workflow_runs_the_guarded_command_once_and_charges_its_item():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    steps = job["steps"]
    run = next(s for s in steps if "meridian_access.ai final " in str(s.get("run", "")))
    assert "--final-score held_out_a1" in run["run"]
    assert '--out "$RUNNER_TEMP/access-final"' in run["run"]  # outside the checkout
    assert run["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert run["env"]["ACCESS_AI_LIVE"] == "1"
    assert run["env"]["ACCESS_CLAUDE_SPEND_LEDGER"] == "out/access-claude-spend.jsonl"
    assert ITEM_ENV not in run["env"]  # the code charges aac-m7-final-score itself
    limit = float(run["env"]["ACCESS_CLAUDE_RUN_LIMIT_USD"])
    assert limit <= load_budget().items[final.BUDGET_ITEM].cap_usd
    check = next(s for s in steps if "final-check" in str(s.get("run", "")))
    fetch = next(s for s in steps if "gh run download" in str(s.get("run", "")))
    assert steps.index(check) < steps.index(fetch) < steps.index(run)
    assert "access-ai-final.yml access-ai-compare.yml" in fetch["run"]
    assert "-n access-ai-ledger -D out/" in fetch["run"]
    # only this step and nothing else sees the key
    assert str(data).count("secrets.") == 1


def test_the_final_workflow_uploads_the_ledger_and_the_results_always():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    uploads = {
        s["with"]["name"]: s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }
    assert set(uploads) == {"access-ai-ledger", "access-final-score"}
    assert all(u["if"] == "always()" for u in uploads.values())
    assert uploads["access-ai-ledger"]["with"]["retention-days"] == 90
    assert uploads["access-final-score"]["with"]["path"] == "${{ runner.temp }}/access-final"
    assert not any("upload-pages-artifact" in str(s.get("uses", "")) for s in job["steps"])


def test_the_run_limit_leaves_room_for_three_repeats_of_a_bigger_world(world_dir):
    """The held-out size is sealed; the limit allows ten times the tuning world's worst case at
    k=3, and the run refuses before any call if even that is not enough."""
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    run = next(s for s in job["steps"] if "meridian_access.ai final " in str(s.get("run", "")))
    limit = float(run["env"]["ACCESS_CLAUDE_RUN_LIMIT_USD"])
    assert limit >= 10 * final.dry_check(world_dir, k=3)["worst_case_usd"]
