"""The plan workflow: nightly, pinned, secrets only for the App token, the App key only from the
access-plan-bot environment on main, skipped cleanly without the App; no Slack token or webhook
is in the repo."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "access-plan.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
COMPARE = ROOT / ".github" / "workflows" / "access-ai-compare.yml"
THIS = Path(__file__).resolve().relative_to(ROOT).as_posix()
KEY_ENVIRONMENT = "access-plan-bot"
MAIN_ONLY = "github.ref == 'refs/heads/main'"
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


# Actions the plan pull-request job adds, pinned here by full SHA (checked against the tags
# when they were added: download-artifact v8.0.2, create-github-app-token v3.3.0).
NEW_PINS = {
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "actions/create-github-app-token": "64ab729a6294000f5aa4a4bc0b73d1f32bc45510",
}


def code_of(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def jobs():
    data, _ = load()
    return data["jobs"]


def test_it_runs_nightly_and_by_hand_and_never_on_pull_requests():
    _, triggers = load()
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    (cron,) = triggers["schedule"]
    assert re.fullmatch(r"\d+ \d+ \* \* \*", cron["cron"])  # once a day


def test_permissions_are_empty_at_the_top_and_read_only_in_every_job():
    data, _ = load()
    assert data["permissions"] == {}
    assert set(data["jobs"]) == {"plan", "leaver-drill", "bot", "pull-requests"}
    assert data["jobs"]["plan"]["permissions"] == {"contents": "read"}
    assert data["jobs"]["leaver-drill"]["permissions"] == {"contents": "read"}
    assert data["jobs"]["bot"]["permissions"] == {}
    # The App token does the writing; GITHUB_TOKEN never gets a write grant.
    assert data["jobs"]["pull-requests"]["permissions"] == {"contents": "read"}
    assert data["concurrency"]["cancel-in-progress"] is False


def test_every_action_is_pinned_to_a_full_sha():
    mine = uses(WORKFLOW)
    assert mine
    known = {**uses(CI), **uses(COMPARE), **NEW_PINS}
    for action, ref in mine.items():
        assert re.fullmatch(r"[0-9a-f]{40}", ref), action
        assert known.get(action) == ref, f"{action} is not pinned to a known SHA"


def test_checkout_keeps_no_credentials_and_the_plan_steps_are_the_documented_ones():
    for job in jobs().values():
        for step in job["steps"]:
            if "checkout" in step.get("uses", ""):
                assert step["with"]["persist-credentials"] is False
    steps = jobs()["plan"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "uv sync --locked" in runs
    assert "make access-data" in runs
    assert "python -m meridian_access.plan --world out/access/seed-6108" in runs
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    for name in ("plan.json", "PLAN.md", "summary.md", "slack-message.json", "timing.json"):
        assert f"{name}" in upload["with"]["path"]
    assert "out/access/plan/held/" in upload["with"]["path"]  # the held plan, if there is one


def test_the_plan_job_has_no_secrets_and_the_model_summary_is_never_switched_on():
    text = WORKFLOW.read_text(encoding="utf-8")
    plan = yaml.safe_dump(jobs()["plan"])
    assert "secrets." not in plan and "vars." not in plan
    assert "ANTHROPIC" not in code_of(text)
    assert "ACCESS_AI_LIVE" not in code_of(text)  # only the comment mentions it
    # The plan job has no environment and no `if`: it builds the plan from any branch.
    assert "environment" not in jobs()["plan"] and "if" not in jobs()["plan"]


def test_every_job_that_reads_the_app_key_is_in_its_environment_and_runs_from_main_only():
    readers = {
        job_id
        for job_id, job in jobs().items()
        if "secrets.ACCESS_APP_PRIVATE_KEY" in yaml.safe_dump(job, width=10_000)
    }
    # bot only compares the key with empty, but an environment secret is empty to a job that
    # doesn't name the environment, so bot needs it too, or it would skip the pull requests.
    assert readers == {"bot", "pull-requests"}
    for job_id in readers:
        job = jobs()[job_id]
        assert job["environment"] == KEY_ENVIRONMENT, job_id
        assert job["if"].startswith(MAIN_ONLY), job_id
        assert "||" not in job["if"], job_id


def test_no_other_workflow_names_the_app_key():
    others = [
        p.name
        for p in sorted(WORKFLOW.parent.iterdir())
        if p != WORKFLOW and "ACCESS_APP_PRIVATE_KEY" in p.read_text(encoding="utf-8")
    ]
    assert others == []
    assert KEY_ENVIRONMENT not in "".join(
        p.read_text(encoding="utf-8") for p in WORKFLOW.parent.iterdir() if p != WORKFLOW
    )


def test_a_missing_app_id_or_key_skips_the_pull_request_job_cleanly():
    bot = jobs()["bot"]
    (check,) = bot["steps"]
    assert "uses" not in check  # no checkout, no code: only the two comparisons
    assert check["env"] == {
        "HAS_APP_ID": "${{ vars.ACCESS_APP_ID != '' }}",
        "HAS_KEY": "${{ secrets.ACCESS_APP_PRIVATE_KEY != '' }}",
    }
    assert bot["outputs"]["ready"] == "${{ steps.check.outputs.ready }}"
    prs = jobs()["pull-requests"]
    assert prs["needs"] == ["plan", "bot"]
    assert prs["if"] == f"{MAIN_ONLY} && needs.bot.outputs.ready == 'yes'"
    # The check itself never fails the run: both branches write an output and exit 0.
    script = check["run"]
    assert "exit 1" not in script and 'echo "ready=yes"' in script and 'echo "ready=no"' in script


def test_the_check_script_says_yes_only_when_both_are_set(tmp_path):
    script = jobs()["bot"]["steps"][0]["run"]
    for has_id, has_key, want in (
        ("true", "true", "yes"),
        ("false", "true", "no"),
        ("true", "false", "no"),
        ("false", "false", "no"),
    ):
        out = tmp_path / f"out-{has_id}-{has_key}"
        done = subprocess.run(  # noqa: S603 - fixed script from the workflow under test
            ["bash", "-c", script],  # noqa: S607
            env={
                "HAS_APP_ID": has_id,
                "HAS_KEY": has_key,
                "GITHUB_OUTPUT": str(out),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0
        assert out.read_text() == f"ready={want}\n"


def test_secrets_appear_only_in_the_check_and_the_token_step():
    text = code_of(WORKFLOW.read_text(encoding="utf-8"))
    assert text.count("secrets.ACCESS_APP_PRIVATE_KEY") == 2
    assert len(re.findall(r"secrets\.", text)) == 2
    steps = jobs()["pull-requests"]["steps"]
    token = next(s for s in steps if "create-github-app-token" in s.get("uses", ""))
    assert token["with"] == {
        "app-id": "${{ vars.ACCESS_APP_ID }}",
        "private-key": "${{ secrets.ACCESS_APP_PRIVATE_KEY }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }
    # The token goes to the bot step only, and nothing runs after it.
    assert steps[-1]["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"  # noqa: S105
    assert "meridian_access.plan_pr --plan-dir out/access/plan" in steps[-1]["run"]
    assert steps.index(token) == len(steps) - 2
    assert sum("steps.app.outputs.token" in yaml.safe_dump(s) for s in steps) == 1


def test_the_pull_request_job_uses_the_plan_the_plan_job_built():
    steps = jobs()["pull-requests"]["steps"]
    fetch = next(s for s in steps if "download-artifact" in s.get("uses", ""))
    assert fetch["with"] == {"name": "access-plan", "path": "out/access/plan"}
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "meridian_access.plan " not in runs and "make access-data" not in runs


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


def test_the_leaver_drill_is_its_own_job_and_keeps_its_result_apart_from_the_plan():
    """Milestone 5's 5-minute leaver plan, measured in the workflow. The drill's job needs no
    other job and nothing needs it, so a failed drill never holds back the plan pull requests;
    its result is its own artifact, so the plan artifact keeps the paths plan_pr reads."""
    data, _ = load()
    drill = data["jobs"]["leaver-drill"]
    assert "needs" not in drill and "if" not in drill
    assert all("leaver-drill" not in str(job.get("needs", "")) for job in data["jobs"].values())
    text = yaml.safe_dump(drill)
    assert "secrets." not in text and "ACCESS_AI_LIVE" not in text
    runs = "\n".join(s.get("run", "") for s in drill["steps"])
    assert "make access-data" in runs
    assert (
        "python -m meridian_access.leaver_drill --world out/access/seed-6108 "
        "--out out/access/leaver-drill"
    ) in runs
    (upload,) = [s for s in drill["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "access-leaver-drill"
    assert upload["with"]["path"] == "out/access/leaver-drill/leaver-drill.json"
    plan_upload = next(
        s for s in data["jobs"]["plan"]["steps"] if "upload-artifact" in s.get("uses", "")
    )
    assert "leaver" not in plan_upload["with"]["path"]
