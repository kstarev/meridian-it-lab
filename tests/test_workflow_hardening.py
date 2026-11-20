"""Every workflow in .github/workflows/ keeps the hardening rules.

docs/github-actions-hardening.md explains each rule in plain words. The rules run on every
workflow file found, so a new workflow is covered without a change here. Each rule also runs
on a small broken workflow, to show that it can fail.
"""

import re
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"})
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

# The workflows that make paid API calls. They are named so the paid-job rule can't pass by
# finding no paid job at all. A new paid workflow is still found by its own text.
PAID_WORKFLOWS = {"access-ai-compare.yml", "access-ai-final.yml", "access-red-team.yml"}
PAID_MARKERS = ("ANTHROPIC_API_KEY", "ACCESS_AI_LIVE", "--no-dry-run")
MAIN_ONLY = "github.ref == 'refs/heads/main'"

# Every write permission any job holds, as (workflow, job, scope). Publishing the site needs
# these two (actions/deploy-pages). A new one has to be added here on purpose.
WRITE_GRANTS = {("pages.yml", "deploy", "pages"), ("pages.yml", "deploy", "id-token")}

# Events a pull request from a fork can start.
PULL_REQUEST_EVENTS = {"pull_request", "pull_request_review", "pull_request_review_comment"}
# Events that run with the repository's secrets after something a fork can start.
FORBIDDEN_EVENTS = {"pull_request_target", "workflow_run"}

PINNED = re.compile(r"[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
VERSION_COMMENT = re.compile(r"\s+#\s*v\d+(\.\d+){0,2}\s*")
OTHER_SECRET = re.compile(r"secrets\.(?!GITHUB_TOKEN\b)\w+")


def parse(text):
    """The workflow and its triggers as a dict (YAML reads a bare `on` as True)."""
    data = yaml.safe_load(text)
    triggers = data.get("on", data.get(True))
    if isinstance(triggers, str):
        triggers = {triggers: None}
    elif isinstance(triggers, list):
        triggers = dict.fromkeys(triggers)
    return data, triggers


def code_of(node):
    """A parsed node as text again. Comments are gone, so only what runs is searched."""
    return yaml.safe_dump(node, sort_keys=False, width=10_000)


def used(data):
    """Every `uses:` of a workflow: its steps, and jobs that call a reusable workflow."""
    for job_id, job in data["jobs"].items():
        if "uses" in job:
            yield job_id, job["uses"]
        for step in job.get("steps", []):
            if "uses" in step:
                yield job_id, step["uses"]


def pin_lines(text):
    """(action, sha, trailing comment) of every `uses:` line that is not a local action."""
    for line in text.splitlines():
        found = USES_LINE.match(line)
        if found and not found.group(1).startswith("./"):
            action, _, sha = found.group(1).partition("@")
            yield action, sha, found.group(2)


# The rules. Each takes a workflow's text and returns what breaks the rule, or nothing.


def unpinned_actions(text):
    data, _ = parse(text)
    return [
        f"{job_id}: {ref}"
        for job_id, ref in used(data)
        if not ref.startswith("./") and not PINNED.fullmatch(ref)
    ]


def pins_without_a_version_comment(text):
    return [
        f"{action}@{sha}"
        for action, sha, comment in pin_lines(text)
        if not VERSION_COMMENT.fullmatch(comment)
    ]


def permission_problems(text):
    data, _ = parse(text)
    found = []
    if data.get("permissions") != {}:
        found.append("the top-level permissions are not {}")
    for job_id, job in data["jobs"].items():
        if not isinstance(job.get("permissions"), dict):
            found.append(f"{job_id}: no permissions of its own")
    return found


def write_grants(name, text):
    data, _ = parse(text)
    return {
        (name, job_id, scope)
        for job_id, job in data["jobs"].items()
        if isinstance(job.get("permissions"), dict)
        for scope, level in job["permissions"].items()
        if level == "write"
    }


def forbidden_events(text):
    _, triggers = parse(text)
    return sorted(FORBIDDEN_EVENTS & set(triggers))


def paid_jobs(text):
    """The jobs that hold the API key or switch live calls on, by their own text."""
    data, _ = parse(text)
    shared = code_of(data.get("env", {}))
    return {
        job_id: job
        for job_id, job in data["jobs"].items()
        if any(marker in code_of(job) + shared for marker in PAID_MARKERS)
    }


def paid_jobs_without_the_gate(text):
    _, triggers = parse(text)
    found = []
    for job_id, job in paid_jobs(text).items():
        environment = job.get("environment")
        if isinstance(environment, dict):
            environment = environment.get("name")
        if environment != "access-ai":
            found.append(f"{job_id}: not in the access-ai environment")
        if set(triggers) != {"workflow_dispatch"}:
            found.append(f"{job_id}: its workflow starts on more than workflow_dispatch")
        if job.get("if") != MAIN_ONLY:
            found.append(f"{job_id}: no main-only `if`")
    return found


def secrets_in_pull_request_workflows(text):
    """`github.token` and `secrets.GITHUB_TOKEN` are fine: a fork's run gets them read-only."""
    data, triggers = parse(text)
    if not PULL_REQUEST_EVENTS & set(triggers):
        return []
    found = sorted(set(OTHER_SECRET.findall(code_of(data))))
    found += [
        f"{job_id}: hands secrets on" for job_id, job in data["jobs"].items() if "secrets" in job
    ]
    return found


def expressions_in_run_scripts(text):
    """Stricter than `inputs.` and `github.event.` alone: no `${{ }}` in a script at all."""
    data, _ = parse(text)
    return [
        f"{job_id}: {step.get('name', 'a step with no name')}"
        for job_id, job in data["jobs"].items()
        for step in job.get("steps", [])
        if "${{" in str(step.get("run", ""))
    ]


def checkouts_that_keep_the_token(text):
    data, _ = parse(text)
    return [
        job_id
        for job_id, job in data["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with", {}).get("persist-credentials") is not False
    ]


RULES = (
    unpinned_actions,
    pins_without_a_version_comment,
    permission_problems,
    forbidden_events,
    paid_jobs_without_the_gate,
    secrets_in_pull_request_workflows,
    expressions_in_run_scripts,
    checkouts_that_keep_the_token,
)


def across_the_repo(rule):
    """Workflow name -> what the rule found, for every workflow that breaks it."""
    found = {}
    for path in WORKFLOWS:
        problems = rule(path.read_text(encoding="utf-8"))
        if problems:
            found[path.name] = problems
    return found


# The rules on the repo's own workflows.


def test_the_workflows_are_found():
    names = {p.name for p in WORKFLOWS}
    assert {"ci.yml", "pages.yml", "access-plan.yml", "access-approvals.yml"} <= names
    assert names >= PAID_WORKFLOWS


def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version_in_a_comment():
    assert across_the_repo(unpinned_actions) == {}
    assert across_the_repo(pins_without_a_version_comment) == {}


def test_an_action_has_the_same_pin_in_every_workflow():
    pins = {}
    for path in WORKFLOWS:
        for action, sha, comment in pin_lines(path.read_text(encoding="utf-8")):
            pins.setdefault(action, set()).add((sha, comment.strip()))
    assert pins
    assert {action: found for action, found in pins.items() if len(found) != 1} == {}


def test_the_token_starts_with_no_permissions_and_every_job_states_its_own():
    assert across_the_repo(permission_problems) == {}


def test_the_only_write_permissions_are_the_two_that_publish_the_site():
    found = set()
    for path in WORKFLOWS:
        found |= write_grants(path.name, path.read_text(encoding="utf-8"))
    assert found == WRITE_GRANTS


def test_no_workflow_starts_on_an_event_that_runs_a_forks_change_with_secrets():
    assert across_the_repo(forbidden_events) == {}


def test_a_workflow_that_runs_on_pull_requests_names_no_secret():
    on_pull_requests = {
        p.name
        for p in WORKFLOWS
        if PULL_REQUEST_EVENTS & set(parse(p.read_text(encoding="utf-8"))[1])
    }
    assert {"ci.yml", "access-approvals.yml"} <= on_pull_requests
    assert across_the_repo(secrets_in_pull_request_workflows) == {}


def test_every_paid_job_waits_for_the_owners_approval_and_runs_from_main_only():
    for name in sorted(PAID_WORKFLOWS):
        assert paid_jobs((WORKFLOW_DIR / name).read_text(encoding="utf-8")), name
    assert across_the_repo(paid_jobs_without_the_gate) == {}


def test_no_run_script_has_an_expression_in_it():
    assert across_the_repo(expressions_in_run_scripts) == {}


def test_no_checkout_leaves_the_token_on_disk():
    assert across_the_repo(checkouts_that_keep_the_token) == {}


# The rules on a small workflow, first as it should be and then broken one way at a time.

SHA = "@0123456789abcdef0123456789abcdef01234567"
KEY = "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}"
GOOD = f"""\
on:
  workflow_dispatch:
permissions: {{}}
jobs:
  paid:
    runs-on: ubuntu-24.04
    environment: access-ai
    if: github.ref == 'refs/heads/main'
    permissions:
      contents: read
    steps:
      - uses: actions/checkout{SHA} # v1.2.3
        with:
          persist-credentials: false
      - name: Run the tool
        env:
          {KEY}
          K: ${{{{ inputs.k }}}}
        run: tool --k "$K"
"""


def broken(old, new):
    assert old in GOOD
    return GOOD.replace(old, new)


def test_the_example_workflow_passes_every_rule():
    for rule in RULES:
        assert rule(GOOD) == [], rule.__name__
    assert write_grants("example.yml", GOOD) == set()


def test_the_pin_rules_fail_on_a_tag_a_short_sha_and_a_missing_version():
    assert unpinned_actions(broken(SHA, "@v1"))
    assert unpinned_actions(broken(SHA, "@0123456"))
    assert pins_without_a_version_comment(broken(" # v1.2.3", ""))
    assert unpinned_actions(broken(f"actions/checkout{SHA}", "./.github/actions/local")) == []


def test_the_permission_rules_fail_on_a_missing_or_wide_grant():
    assert permission_problems(broken("permissions: {}\n", ""))
    assert permission_problems(broken("permissions: {}", "permissions: write-all"))
    assert permission_problems(broken("    permissions:\n      contents: read\n", ""))
    wide = write_grants("example.yml", broken("contents: read", "contents: write"))
    assert wide == {("example.yml", "paid", "contents")}


def test_the_event_rule_fails_on_pull_request_target_and_workflow_run():
    assert forbidden_events(broken("workflow_dispatch:", "pull_request_target:"))
    assert forbidden_events(broken("workflow_dispatch:", "workflow_run:\n    workflows: [CI]"))


def test_the_pull_request_rule_fails_on_a_secret_and_allows_the_default_token():
    assert secrets_in_pull_request_workflows(broken("workflow_dispatch:", "pull_request:"))
    default = GOOD.replace("workflow_dispatch:", "pull_request:").replace(
        KEY, "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}"
    )
    assert secrets_in_pull_request_workflows(default) == []


def test_the_paid_rule_finds_a_paid_job_three_ways_and_fails_without_each_part_of_the_gate():
    assert paid_jobs(GOOD)
    assert paid_jobs(broken(KEY, 'ACCESS_AI_LIVE: "1"'))
    assert paid_jobs(broken(KEY, "A: b").replace("tool --k", "tool --no-dry-run --k"))
    assert not paid_jobs(broken(KEY, "A: b"))
    assert paid_jobs_without_the_gate(broken("    environment: access-ai\n", ""))
    assert paid_jobs_without_the_gate(broken("    if: github.ref == 'refs/heads/main'\n", ""))
    nightly = "workflow_dispatch:\n  schedule:\n    - cron: '0 3 * * *'"
    assert paid_jobs_without_the_gate(broken("workflow_dispatch:", nightly))


def test_the_script_rule_fails_on_an_input_or_an_event_field_in_a_script():
    assert expressions_in_run_scripts(broken('"$K"', "${{ inputs.k }}"))
    assert expressions_in_run_scripts(broken('"$K"', "${{ github.event.pull_request.title }}"))


def test_the_checkout_rule_fails_when_the_token_is_kept():
    assert checkouts_that_keep_the_token(broken("persist-credentials: false", "fetch-depth: 1"))
    assert checkouts_that_keep_the_token(
        broken("persist-credentials: false", "persist-credentials: true")
    )


# Dependabot.


def dependabot():
    config = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    return config, {update["package-ecosystem"]: update for update in config["updates"]}


def test_dependabot_updates_the_actions_and_the_uv_lock_in_few_pull_requests():
    config, updates = dependabot()
    assert config["version"] == 2
    # uv, not pip: the pip ecosystem leaves uv.lock alone, and CI installs with --locked.
    assert {"github-actions", "uv"} <= set(updates)
    assert "pip" not in updates
    assert (ROOT / "uv.lock").is_file()
    for update in updates.values():
        assert update["directory"] == "/"
        assert update["schedule"]["interval"] == "weekly"
        assert update["open-pull-requests-limit"] <= 3
        (group,) = update["groups"].values()
        assert group == {"patterns": ["*"], "update-types": ["minor", "patch"]}


def test_dependabot_never_looks_up_the_workspaces_own_packages():
    _, updates = dependabot()
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    own = {name for name, source in root["tool"]["uv"]["sources"].items() if source["workspace"]}
    assert own
    assert {entry["dependency-name"] for entry in updates["uv"]["ignore"]} == own


def test_the_docs_page_names_only_tests_that_are_in_this_file():
    page = (ROOT / "docs" / "github-actions-hardening.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`(test_\w+)`", page))
    assert len(named) >= 10
    assert named <= {name for name in globals() if name.startswith("test_")}


# Two more rules from the review of 2026-11-20: where the paid runs take their spend ledger
# from, and what the Pages workflow publishes.

RUN_LOOKUP = "gh run list"
MAIN_RUNS_ONLY = ("--branch main", "--event workflow_dispatch")
PAGES_WORKFLOW = WORKFLOW_DIR / "pages.yml"


def run_lookups_off_main(text):
    """`gh run list` commands that could pick a run started on another branch.

    The paid workflows take the spend ledger from the newest earlier run. A run of the same
    workflow file started on another branch runs that branch's version of the file, so it could
    upload a ledger with less spend in it. Only main's own manual runs may be used.
    """
    data, _ = parse(text)
    found = []
    for job_id, job in data["jobs"].items():
        for step in job.get("steps", []):
            script = str(step.get("run", "")).replace("\\\n", " ")
            for line in script.splitlines():
                if RUN_LOOKUP in line and not all(flag in line for flag in MAIN_RUNS_ONLY):
                    found.append(f"{job_id}: {line.strip()}")
    return found


def unsafe_site_entries(listing):
    """Entries of `git ls-files -s -- site` that must never be published.

    actions/upload-pages-artifact packs the folder with `tar --dereference`, so a link in site/
    would publish the file it points to, a sealed set included. A submodule or a file with a
    sealed set's name has no place on the site either.
    """
    found = []
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        if meta.split()[0] in {"120000", "160000"}:
            found.append(f"{path}: a link or a submodule")
        elif path.rsplit("/", 1)[-1].startswith("held_out_"):
            found.append(f"{path}: a sealed set's name")
    return found


def a_workflow_that_runs(script):
    lines = "\n".join(f"          {line}" for line in script.splitlines())
    return f"""\
on:
  workflow_dispatch:
permissions: {{}}
jobs:
  fetch:
    runs-on: ubuntu-24.04
    permissions:
      actions: read
    steps:
      - name: Fetch the ledger
        run: |
{lines}
"""


def test_every_run_lookup_takes_mains_own_manual_runs_only():
    with_lookups = {p.name for p in WORKFLOWS if RUN_LOOKUP in p.read_text(encoding="utf-8")}
    # The three paid workflows look the ledger up, so the rule can't pass by finding none.
    assert with_lookups >= PAID_WORKFLOWS
    assert across_the_repo(run_lookups_off_main) == {}


def test_the_run_lookup_rule_fails_without_the_branch_or_the_event():
    lookup = "gh run list --workflow x.yml --status completed --limit 1"
    assert run_lookups_off_main(a_workflow_that_runs(lookup))
    assert run_lookups_off_main(a_workflow_that_runs(f"{lookup} --branch main"))
    assert run_lookups_off_main(a_workflow_that_runs(f"{lookup} --event workflow_dispatch"))
    both = f"{lookup} --branch main --event workflow_dispatch"
    assert run_lookups_off_main(a_workflow_that_runs(both)) == []
    # A command split over lines with `\` is read as one line.
    split = f"{lookup} \\\n  --branch main --event workflow_dispatch \\\n  --json databaseId"
    assert run_lookups_off_main(a_workflow_that_runs(split)) == []
    assert run_lookups_off_main(a_workflow_that_runs(f"{lookup} \\\n  --branch main"))


def test_the_pages_workflow_publishes_site_only_and_site_holds_no_link_or_sealed_name():
    data, _ = parse(PAGES_WORKFLOW.read_text(encoding="utf-8"))
    uploads = [
        step
        for job in data["jobs"].values()
        for step in job.get("steps", [])
        if "upload-pages-artifact" in str(step.get("uses", ""))
    ]
    assert [step["with"]["path"] for step in uploads] == ["site"]
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "site"],  # noqa: S607 - git from PATH, fixed arguments
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "site/index.html" in listing  # the listing is real, so the rule can't pass on nothing
    assert unsafe_site_entries(listing) == []


def test_the_site_rule_fails_on_a_link_a_submodule_and_a_sealed_name():
    ok = "100644 0123456789abcdef0123456789abcdef01234567 0\tsite/index.html"
    assert unsafe_site_entries(ok) == []
    link = "120000 0123456789abcdef0123456789abcdef01234567 0\tsite/data.json"
    assert unsafe_site_entries(link)
    submodule = "160000 0123456789abcdef0123456789abcdef01234567 0\tsite/vendor"
    assert unsafe_site_entries(submodule)
    sealed = "100644 0123456789abcdef0123456789abcdef01234567 0\tsite/held_out_x.jsonl"
    assert unsafe_site_entries(sealed)
