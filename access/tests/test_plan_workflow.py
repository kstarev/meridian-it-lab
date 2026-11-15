"""The plan workflow is manual, pinned and secret-free; no Slack token or webhook is in the repo."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "access-plan.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
COMPARE = ROOT / ".github" / "workflows" / "access-ai-compare.yml"
THIS = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Tracked files allowed to name the forbidden strings: this test only, by exact path.
SLACK_WHITELIST = {THIS}
SLACK_NEEDLES = (
    "hooks.slack" + ".com",
    "xoxb" + "-",
    "xoxp" + "-",
    "xapp" + "-",
    "SLACK" + "_TOKEN",
)


def load():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data, data.get("on", data.get(True))


def uses(path: Path) -> dict[str, str]:
    """action name -> full ref, with the trailing version comment dropped."""
    found = {}
    for m in re.finditer(r"uses:\s*(\S+)@([0-9a-f]{40}|\S+)", path.read_text(encoding="utf-8")):
        found[m.group(1)] = m.group(2)
    return found


def test_it_runs_only_when_dispatched_and_for_now_only():
    _, triggers = load()
    assert set(triggers) == {"workflow_dispatch"}
    comment = WORKFLOW.read_text(encoding="utf-8").replace("\n# ", " ")
    assert "owner's GitHub App, ruleset and secrets" in comment


def test_permissions_are_empty_at_the_top_and_read_only_in_the_job():
    data, _ = load()
    assert data["permissions"] == {}
    (job,) = data["jobs"].values()
    assert job["permissions"] == {"contents": "read"}


def test_every_action_is_pinned_to_a_full_sha_the_other_workflows_use():
    mine = uses(WORKFLOW)
    assert mine
    known = {**uses(CI), **uses(COMPARE)}
    for action, ref in mine.items():
        assert re.fullmatch(r"[0-9a-f]{40}", ref), action
        assert known.get(action) == ref, f"{action} is not pinned to a SHA that ci.yml uses"


def test_checkout_keeps_no_credentials_and_the_steps_are_the_documented_ones():
    data, _ = load()
    (job,) = data["jobs"].values()
    steps = job["steps"]
    checkout = next(s for s in steps if "checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "uv sync --locked" in runs
    assert "make access-data" in runs
    assert "python -m meridian_access.plan --world out/access/seed-6108" in runs
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    for name in ("plan.json", "PLAN.md", "summary.md", "slack-message.json", "timing.json"):
        assert f"{name}" in upload["with"]["path"]
    assert "out/access/plan/held/" in upload["with"]["path"]  # the held plan, if there is one


def test_no_secrets_and_the_model_summary_is_never_switched_on():
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "secrets." not in code
    assert "ANTHROPIC" not in code
    assert "ACCESS_AI_LIVE" not in code  # only the comment mentions it
    assert "environment:" not in code


def test_make_access_plan_writes_into_out_access_plan():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(
        r"^access-plan:.*\n\t.*meridian_access\.plan .*--out out/access/plan", makefile, re.M
    )


def test_no_tracked_file_has_a_slack_token_or_webhook():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607 - fixed
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    hits = []
    for name in filter(None, listed):
        if name in SLACK_WHITELIST or name.endswith(".gz"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(n in text for n in SLACK_NEEDLES):
            hits.append(name)
    assert not hits, hits
