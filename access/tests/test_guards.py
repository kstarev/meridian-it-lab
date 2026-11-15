"""Repo-wide guards: no secret strings, and the AI-compare workflow is manual and pinned."""

import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "access-ai-compare.yml"
SKIP_DIRS = {".git", ".venv", "out", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}
TEXT = {".py", ".md", ".yml", ".yaml", ".json", ".toml", ".txt", ".cfg", ".ini", ".lock", ".sh"}


def test_no_anthropic_key_strings_in_the_repo():
    needle = "sk-" + "ant-"  # split so this file doesn't contain it
    hits = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.startswith(".env") or Path(name).suffix not in TEXT:
                continue  # an .env file is never opened, and no archives either
            path = Path(base) / name
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(path.relative_to(ROOT)))
    assert not hits, hits


UPLOAD_ARTIFACT = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"


def _workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = data.get("on", data.get(True))  # YAML reads a bare `on` as True
    return data, triggers


def test_the_workflow_runs_only_when_dispatched():
    _, triggers = _workflow()
    assert set(triggers) == {"workflow_dispatch"}
    assert "schedule" not in triggers and "push" not in triggers and "pull_request" not in triggers


def test_the_workflow_needs_approval_and_has_least_privilege():
    data, _ = _workflow()
    assert data["permissions"] == {}
    (job,) = data["jobs"].values()
    assert job["environment"] == "access-ai"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["if"] == "github.ref == 'refs/heads/main'"  # only main's code gets the key


def test_every_action_is_pinned_to_a_full_sha_and_checkout_keeps_no_credentials():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    uses = [s for s in job["steps"] if "uses" in s]
    assert uses
    for step in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    checkout = next(s for s in uses if s["uses"].startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    # the pins are the ones CI already uses
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text() + (
        ROOT / ".github" / "workflows" / "pages.yml"
    ).read_text()
    for step in uses:
        if step["uses"].startswith("actions/upload-artifact@"):
            assert step["uses"] == UPLOAD_ARTIFACT  # the pin is new to this repo
        else:
            assert step["uses"] in ci, step["uses"]


def test_the_key_comes_from_a_secret_and_the_compare_is_a_real_run():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    run = next(s for s in job["steps"] if "meridian_access.ai compare" in str(s.get("run", "")))
    assert run["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert run["env"]["ACCESS_AI_LIVE"] == "1"
    assert "--no-dry-run" in run["run"]
    # ADR-037: the run is charged to one budget item, and its run limit fits in that item.
    from meridian_access.ai.budget import load_budget

    item = load_budget().items[run["env"]["ACCESS_CLAUDE_BUDGET_ITEM"]]
    assert item.project == "access"
    assert "ACCESS_CLAUDE_SPEND_CAP_USD" not in run["env"]
    assert float(run["env"]["ACCESS_CLAUDE_RUN_LIMIT_USD"]) <= item.cap_usd


def test_the_cap_holds_across_runs_through_the_ledger_artifact():
    data, _ = _workflow()
    (job,) = data["jobs"].values()
    steps = job["steps"]
    fetch = next(s for s in steps if "gh run download" in str(s.get("run", "")))
    assert fetch["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105 - an expression
    assert (
        "gh run list --workflow access-ai-compare.yml --status completed --limit 1" in fetch["run"]
    )
    assert "-n access-ai-ledger -D out/" in fetch["run"]
    run = next(s for s in steps if "meridian_access.ai compare" in str(s.get("run", "")))
    assert run["env"]["ACCESS_CLAUDE_SPEND_LEDGER"] == "out/access-claude-spend.jsonl"
    # the fetch comes before the compare
    assert steps.index(fetch) < steps.index(run)
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert {u["with"]["name"] for u in uploads} == {"access-ai-ledger", "access-ai-compare"}
    for u in uploads:
        assert u["uses"] == UPLOAD_ARTIFACT and u["if"] == "always()"
        assert u["with"]["retention-days"] == 90
    assert not any("upload-pages-artifact" in str(s.get("uses", "")) for s in steps)
