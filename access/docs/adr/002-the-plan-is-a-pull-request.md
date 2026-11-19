# AAC-002: The plan is one rolling pull request; approval is a review; apply is the merge

**Date:** 2026-11-14 · **Status:** Accepted; built, with the open checks listed in [MILESTONES.md](../../MILESTONES.md). Where plans open was the user's choice (2026-11-14).

**Context:** The project's idea is "terraform plan for people": every access change is shown before it happens, and a person approves it. GitHub pull requests already give a diff, a review, an approval record and a merge, which is the audit trail auditors ask for. The lab repo is public, so every pull request stays in its history for good, and a new one each night would bury everything else.

**Decision:**

- **One rolling plan pull request** in meridian-it-lab, from the bot branch `access/plan` into `main`. Each night the job rebuilds the plan and force-updates that branch:
  - if the plan isn't empty and no plan pull request is open, it opens one;
  - if one is open, it updates it in place;
  - if the plan is empty, it closes the open one.
- **What the pull request holds:** `access/plan/plan.json` (every change, machine-readable, with the hash of the actual state it was built from) and `access/plan/PLAN.md`, the readable version. The pull request description is the plain-English summary ([AAC-006](006-model-choice-and-spend-cap.md)), with a table of counts built by code above it. Each time a nightly update changes the plan, the bot adds a comment saying what changed, so the history of the plan is in the pull request ([AAC-008](008-runs-on-github-only.md)).
- **A second rolling pull request, the held plan.** Buckets over the blast-radius limit and every bulk import are not in the main plan. They go to a second rolling pull request from the bot branch `access/plan-held`, holding `access/plan/held/plan.json` and its `PLAN.md`, with the same open, update and close rules, and it needs **two** code-owner approvals ([AAC-007](007-safety-rails.md)). A plan pull request holds one part only. 2026-11-15: this split was the owner's decision.
- **Sections in every plan:** changes to make, grouped by person; manual tasks for apps without an API; drift (changed outside the process); suggested map entries (AI or rules), which need approval before any change can depend on them; warnings (blast radius, stale data, break-glass accounts seen).
- **Approval is a pull request review** by CODEOWNERS (the IT team; in the lab, the repo owner stands in, as in lab ADR-035). A ruleset on `main` requires a pull request, code-owner review, passing checks and **dismisses stale approvals when new commits are pushed**, so an approval always covers the plan as it is now.
- **Apply is the merge, per part.** The plan has two parts, each its own pull request: the main plan is applied when its pull request is merged with one code-owner approval, and the held plan (bulk-import changes and the days over the limit) when its pull request is merged with two ([AAC-007](007-safety-rails.md): dry run by default, stale-plan check, approvals check). Applying on merge needs the owner's GitHub App and is not built; today the apply workflow is manual and applies only the main part. Applying either part changes the actual state, so the other part becomes stale (same actual-state hash) and is built again and approved again.
- **The bot is a GitHub App** with only the repository permissions it needs (contents and pull requests). Pull requests opened with the default `GITHUB_TOKEN` don't trigger other workflows, so CI wouldn't run on them; an App's token does. The App's private key is an Actions secret. 2026-11-18 (owner's ruling R-G1): it is a secret of the `access-plan-bot` environment, which accepts `main` only, and the jobs that use it run from `main` only ([SETUP.md, part D](../SETUP.md#d-move-the-app-key-into-the-access-plan-bot-environment)).
- **People change policy the normal way:** a pull request that edits `access/policy/`, reviewed like any other. The next nightly plan reflects it.

**Consequences:**

- The public history gets one plan pull request per round of changes, not one per night. Branch auto-delete keeps branches tidy.
- The merged pull requests are the approval record: who approved which change, and when.
- Force-updating the branch dismisses earlier approvals by design. A reviewer who approved Tuesday's plan must approve Wednesday's again.
- Setting up the GitHub App and the ruleset are the user's actions, done in milestone 5.
