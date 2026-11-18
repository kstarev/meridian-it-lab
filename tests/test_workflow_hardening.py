"""Every workflow in .github/workflows/ keeps the hardening rules.

docs/github-actions-hardening.md explains each rule in plain words. The rules run on every
workflow file found, so a new workflow is covered without a change here. Each rule also runs
on a small broken workflow, to show that it can fail.
"""

import re
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

# Every secret a workflow may name besides GITHUB_TOKEN, and the one environment that holds it.
# Each of these environments accepts `main` only (a GitHub setting, see the docs page). A new
# secret has to be added here on purpose.
SECRET_ENVIRONMENTS = {
    "ANTHROPIC_API_KEY": "access-ai",
    "ACCESS_APP_PRIVATE_KEY": "access-plan-bot",
}

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


def environment_of(job):
    environment = job.get("environment")
    if isinstance(environment, dict):
        environment = environment.get("name")
    return environment


def runs_from_main_only(job):
    """The job's `if` is the main-only test, alone or ANDed with more (never ORed)."""
    condition = str(job.get("if", "")).strip()
    return condition == MAIN_ONLY or (
        condition.startswith(f"{MAIN_ONLY} && ") and "||" not in condition
    )


def paid_jobs_without_the_gate(text):
    _, triggers = parse(text)
    found = []
    for job_id, job in paid_jobs(text).items():
        if environment_of(job) != "access-ai":
            found.append(f"{job_id}: not in the access-ai environment")
        if set(triggers) != {"workflow_dispatch"}:
            found.append(f"{job_id}: its workflow starts on more than workflow_dispatch")
        if job.get("if") != MAIN_ONLY:
            found.append(f"{job_id}: no main-only `if`")
    return found


def secret_jobs_without_their_environment(text):
    """A job that names a secret other than GITHUB_TOKEN gets it from its environment, on main.

    The secret must be one of SECRET_ENVIRONMENTS, the job must name that secret's environment
    (an environment secret reaches no other job), and its `if` must keep it to main. A secret
    in the workflow's own `env:` would reach every job, so that is not allowed either.
    """
    data, _ = parse(text)
    found = [
        f"workflow env: {name}"
        for name in sorted(set(OTHER_SECRET.findall(code_of(data.get("env", {})))))
    ]
    for job_id, job in data["jobs"].items():
        for name in sorted(
            {s.removeprefix("secrets.") for s in OTHER_SECRET.findall(code_of(job))}
        ):
            want = SECRET_ENVIRONMENTS.get(name)
            if want is None:
                found.append(f"{job_id}: {name} is not a known secret")
            elif environment_of(job) != want:
                found.append(f"{job_id}: reads {name} but is not in the {want} environment")
            if not runs_from_main_only(job):
                found.append(f"{job_id}: reads {name} without a main-only `if`")
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
    secret_jobs_without_their_environment,
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


def test_every_job_that_reads_a_secret_gets_it_from_its_environment_on_main_only():
    named = {
        name.removeprefix("secrets.")
        for path in WORKFLOWS
        for name in OTHER_SECRET.findall(code_of(parse(path.read_text(encoding="utf-8"))[0]))
    }
    # Every listed secret is still in use, so the rule can't pass by finding no secret at all.
    assert named == set(SECRET_ENVIRONMENTS)
    assert across_the_repo(secret_jobs_without_their_environment) == {}


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


def test_the_secret_rule_fails_without_the_environment_or_the_main_only_if():
    no_env = broken("    environment: access-ai\n", "")
    assert secret_jobs_without_their_environment(no_env)
    assert secret_jobs_without_their_environment(
        broken("environment: access-ai", "environment: access-plan-bot")
    )
    assert secret_jobs_without_their_environment(
        broken("    if: github.ref == 'refs/heads/main'\n", "")
    )
    either = "    if: github.ref == 'refs/heads/main' && needs.a.result == 'success' || always()\n"
    assert secret_jobs_without_their_environment(
        broken("    if: github.ref == 'refs/heads/main'\n", either)
    )
    unknown = broken("secrets.ANTHROPIC_API_KEY", "secrets.SOME_OTHER_KEY")
    assert secret_jobs_without_their_environment(unknown)
    shared = broken(
        "permissions: {}\n", "permissions: {}\nenv:\n  X: ${{ secrets.ANTHROPIC_API_KEY }}\n"
    )
    assert secret_jobs_without_their_environment(shared)
    both = "    if: github.ref == 'refs/heads/main' && needs.a.outputs.ready == 'yes'\n"
    assert (
        secret_jobs_without_their_environment(
            broken("    if: github.ref == 'refs/heads/main'\n", both)
        )
        == []
    )
    # The dict form of `environment:` counts too.
    named = broken("environment: access-ai", "environment:\n      name: access-ai")
    assert secret_jobs_without_their_environment(named) == []


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
