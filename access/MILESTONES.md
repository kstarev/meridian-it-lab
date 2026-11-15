# Access-as-Code: milestones

Each milestone is one branch and one pull request, merged by the repo owner. "Done when" lists the checks that must pass; each one is a test, a CI run or something a reviewer can see. Rules that hold in every milestone:

- Fake data only, `@meridian.example`, mocks only.
- `make lint` and the full test suite pass in CI, tier0's tests included.
- Nothing is tuned on the held-out world, and the builder never reads its recipe ([AAC-005](docs/adr/005-sealed-answer-key.md)).
- Every published number comes from a report and is checked by a test.

## 1. Brief and decisions (this pull request)

The brief, decisions AAC-001 to AAC-008 and this plan. No code.

**Done when:**

- [ ] `access/BRIEF.md`, `access/MILESTONES.md` and `access/docs/adr/` are merged.
- [ ] The lab brief points to Access-as-Code's brief.

## 2. Data and needles

The access world in the shared dataset ([AAC-004](docs/adr/004-extend-the-shared-dataset.md)): 230 people, an HRIS timeline over a simulated month, Okta, Google, Slack, 95 messy CSV exports, the planted problems on the tuning world and the sealed held-out recipe.

**Done when:**

- [ ] `make access-data` writes the tuning world to `out/access/`, offline, with no credentials.
- [ ] Same seed, same bytes: a golden hash test for the access world.
- [ ] Every tier0 world (tuning, v1, v2, v3) still has its old hash.
- [ ] The tuning world's planted counts match the brief's table (23 orphaned accounts, 11 movers, and the rest as set here), checked by a test from the answer key.
- [ ] The 95 exports use at least 4 column layouts and the 4 date formats, and some list usernames only, checked by a test.
- [ ] The answer key is written apart from everything the code under test reads, and a test fails if any collector input contains an answer-key field.
- [ ] A blind helper has written the held-out recipe; it's sealed by SHA-256 with count-only tests, and `--final-score=held_out_a1` is the only way to build it.
- [ ] The held-out recipe brings mess the tuning recipe doesn't have (new headers, date formats, username patterns, name spellings, an encoding and custom HR cases), checked yes/no by tests ([AAC-009](docs/adr/009-held-out-recipe-can-bring-new-mess.md)).
- [ ] The held-out answer key passes the code checks (references, duplicates and contradictions, counts against the recipe, completeness from the world), and a second blind reviewer reports PASS.
- [ ] A blind referee, written from the raw held-out files and the public guide only, builds its own key. Its per-type comparison with the generator's key is logged, and every disagreement has the owner's ruling before any held-out number is published ([AAC-011](docs/adr/011-blind-referee-against-shared-mistakes.md)).
- [ ] The package is a workspace member with its tests in CI.

## 3. Policy YAML and desired state

Policy as code and the code that turns HRIS records into what each person should have.

**Done when:**

- [ ] `access/policy/` holds roles, apps, birthright apps, exceptions (each with an expiry and an approval reference), the break-glass allowlist and settings (5 people per day, 50 accesses per day), validated against a schema in CI.
- [ ] The roles, birthright and app lists in `access/policy/` are built from the public access policy table in `dataset/ACCESS_RECIPE.md` (the one the generator uses), and a test fails if the two drift apart.
- [ ] Desired state for every tuning-world person is built from HRIS + policy + the reviewed title map, deterministically.
- [ ] Contractors past their end date, leavers and people on leave get the expected desired state, checked against the tuning answer key.
- [ ] An unmapped job title gets birthright apps only, and is listed for a decision.
- [ ] Expired exceptions stop granting access, checked by a test with a fixed clock.
- [ ] `.github/CODEOWNERS` covers `access/policy/`.

## 4. Collectors and identity matching

Reading actual state from the three mocks and the 95 exports, and matching every account to a person.

**Done when:**

- [ ] The Okta, Google and Slack mocks pass contract tests against recorded response shapes, including paging, `429` with `Retry-After` and transient `5xx` ([AAC-003](docs/adr/003-mock-api-shape.md)).
- [ ] Collectors hold read-only tokens, and a test shows a write with one fails.
- [ ] All 95 exports parse: every column layout and date format, with a report of rows that can't be read (none silently dropped).
- [ ] Rules-only matching is scored on the tuning world: precision and recall on account-to-person pairs, with intervals.
- [ ] The AI's title and match suggestions run through the spend ledger and cap ($3, `ACCESS_CLAUDE_SPEND_CAP_USD`), with structured outputs validated by code; a refusal or malformed answer counts as no suggestion.
- [ ] Haiku 5.5, Sonnet 5.5 and Opus 5.5 are compared on the tuning world only, in a GitHub Actions run approved by the owner; the report shows accuracy, precision, recall and cost, and the user picks a model ([AAC-006](docs/adr/006-model-choice-and-spend-cap.md)).
- [ ] The chosen prompts are frozen by hash.

## 5. Diff, plan pull request and the mock Slack message

The diff, `plan.json` and `PLAN.md`, the rolling plan pull request and the mock Slack message ([AAC-002](docs/adr/002-the-plan-is-a-pull-request.md), [AAC-008](docs/adr/008-runs-on-github-only.md)).

**Done when:**

- [ ] The diff finds every planted problem type on the tuning world, with precision and recall per type reported.
- [ ] `plan.json` records the actual-state hash, and the same inputs give byte-identical plans.
- [ ] A leaver's full deprovisioning plan is ready in under 5 minutes from the HRIS change, measured in the workflow.
- [ ] Changes resting on unreviewed AI suggestions never appear in a plan; they appear only as suggested map entries (a test).
- [ ] The summary's names, apps and numbers all match `plan.json`, or the template summary is used (a test with a mocked model).
- [ ] The user has created the GitHub App, the ruleset on `main` (pull request, code-owner review, checks, stale approvals dismissed) and the secrets (the App key and the Anthropic key).
- [ ] The scheduled workflow opens, updates and closes the one rolling plan pull request on the tuning world's simulated month, with the summary in the description and a comment each time the plan changes.
- [ ] The mock Slack adapter builds the Block Kit message (header, counts, warnings, pull request link), writes it to `out/access/slack-message.json`, and the workflow uploads it as an artifact.
- [ ] Tests check the message: valid Block Kit structure, text within Slack's length limits, every number matching `plan.json`, nothing beyond what the pull request shows.
- [ ] Nothing in Access-as-Code sends to Slack: the adapter has no network code, there is no Slack token or webhook anywhere, and a test fails if one appears.
- [ ] The workflows pass the lab's baseline: actions pinned to SHAs, `permissions: {}` with per-job grants, no stored checkout credentials.

## 6. Apply, drift and blast radius

Merging the plan applies it to the mocks, safely ([AAC-007](docs/adr/007-safety-rails.md)).

**Done when:**

- [ ] Apply is a dry run unless `ACCESS_APPLY_MODE=mock-live`, and there is no mode for real systems.
- [ ] Applying the same plan twice changes nothing the second time, including after injected `429` and `5xx` errors.
- [ ] A stale plan (actual state changed since it was built) is refused, and the next plan is rebuilt.
- [ ] The plan is split per cause-day: a day with 5 or more people or 50 or more removals (and the no-date bucket, and every bulk import) goes to a held plan that needs two approvals and can't be applied with one: the required check fails, and apply checks again; the rest is the main plan with one approval. The planted mass termination is caught this way.
- [ ] No code path changes a break-glass account (a test over every change type).
- [ ] Planted drift is flagged in the plan pull request and the mock Slack message, scored by precision and recall.
- [ ] Non-SSO removals become manual tasks, confirmed against the next export.
- [ ] N is reviewed against the tuning data, and changed by a policy pull request if needed.

## 7. Results page

The sealed final score and a page that explains it.

**Done when:**

- [ ] The held-out world is scored once, with the frozen prompts and the chosen model, in an owner-approved GitHub Actions run.
- [ ] Every disagreement between the tool and the key has the owner's ruling in the rulings log before any number is published; key errors are reported separately and not counted against the tool ([AAC-010](docs/adr/010-human-rulings-before-publishing.md)).
- [ ] The report is published in `docs/evals/`.
- [ ] Precision and recall per problem type (target ≥ 0.90 each), identity matching rules only vs rules + AI, the safety counts (target 0) and cost, with intervals.
- [ ] `site/access/` is live at kstarev.dev/meridian-it-lab/access/, deployed by the existing Pages workflow.
- [ ] Every number on the page is checked against the published report by a test, as `test_site_numbers.py` does for tier0.
- [ ] The page states the honest limits from the brief and the compliance mapping as a mapping, not a claim.
- [ ] The held-out world becomes a dev set.
