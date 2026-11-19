# Access-as-Code: milestones

Each milestone is one branch and one pull request, merged by the repo owner. "Done when" lists the checks that must pass; each one is a test, a CI run or something a reviewer can see. Rules that hold in every milestone:

- Fake data only, `@meridian.example`, mocks only.
- `make lint` and the full test suite pass in CI, tier0's tests included.
- Nothing is tuned on the held-out world, and the builder never reads its recipe ([AAC-005](docs/adr/005-sealed-answer-key.md)).
- Every published number comes from a report and is checked by a test.

## 1. Brief and decisions (this pull request)

The brief, decisions AAC-001 to AAC-008 and this plan. No code.

**Done when:**

- [x] `access/BRIEF.md`, `access/MILESTONES.md` and `access/docs/adr/` are merged. Evidence: [#16](https://github.com/kstarev/meridian-it-lab/pull/16).
- [x] The lab brief points to Access-as-Code's brief. Evidence: [docs/brief.md](../docs/brief.md) links it; [#16](https://github.com/kstarev/meridian-it-lab/pull/16).

## 2. Data and needles

The access world in the shared dataset ([AAC-004](docs/adr/004-extend-the-shared-dataset.md)): 230 people, an HRIS timeline over a simulated month, Okta, Google, Slack, 95 messy CSV exports, the planted problems on the tuning world and the sealed held-out recipe.

**Done when:**

- [x] `make access-data` writes the tuning world to `out/access/`, offline, with no credentials. Evidence: [`test_the_access_world_generates`](tests/test_package.py); the plan workflow runs it with no secrets; [#18](https://github.com/kstarev/meridian-it-lab/pull/18).
- [x] Same seed, same bytes: a golden hash test for the access world. Evidence: [`test_same_seed_gives_identical_bytes`](../dataset/tests/test_access_world.py), [`test_golden_manifest_hash`](../dataset/tests/test_access_world.py).
- [x] Every tier0 world (tuning, v1, v2, v3) still has its old hash. Evidence: [`test_tier0_world_hash_unchanged`](../dataset/tests/test_tier0_worlds_unchanged.py).
- [x] The tuning world's planted counts match the brief's table (23 orphaned accounts, 11 movers, and the rest as set here), checked by a test from the answer key. Evidence: [`test_planted_counts_match_the_tuning_recipe`](../dataset/tests/test_access_world.py).
- [x] The 95 exports use at least 4 column layouts and the 4 date formats, and some list usernames only, checked by a test. Evidence: [`test_layouts_formats_and_username_only`](../dataset/tests/test_access_exports.py).
- [x] The answer key is written apart from everything the code under test reads, and a test fails if any collector input contains an answer-key field. Evidence: [`test_answer_key_fields_stay_in_the_answer_key`](../dataset/tests/test_access_exports.py), [`test_actual_state_from_a_directory_has_everything_and_no_answer_key`](tests/test_collect.py).
- [x] A blind helper has written the held-out recipe; it's sealed by SHA-256 with count-only tests, and `--final-score=held_out_a1` is the only way to build it. Evidence: [`test_sealed_by_hash`](../dataset/tests/test_access_held_out_a1.py), [`test_cli_refuses_without_the_final_score_flag`](../dataset/tests/test_access_held_out_a1.py); [#19](https://github.com/kstarev/meridian-it-lab/pull/19).
- [x] The held-out recipe brings mess the tuning recipe doesn't have (new headers, date formats, username patterns, name spellings, an encoding and custom HR cases), checked yes/no by tests ([AAC-009](docs/adr/009-held-out-recipe-can-bring-new-mess.md)). Evidence: [`test_new_mess`](../dataset/tests/test_access_held_out_a1.py), [`test_new_hr_edge_cases`](../dataset/tests/test_access_held_out_a1.py).
- [x] The held-out answer key passes the code checks (references, duplicates and contradictions, counts against the recipe, completeness from the world), and a second blind reviewer reports PASS. Evidence: [`test_held_out_key_passes`](../dataset/tests/test_access_key_checks.py); reviewer PASS (round 3) recorded in [#19](https://github.com/kstarev/meridian-it-lab/pull/19).
- [x] A blind referee, written from the raw held-out files and the public guide only, builds its own key. Its per-type comparison with the generator's key is logged, and every disagreement has the owner's ruling before any held-out number is published ([AAC-011](docs/adr/011-blind-referee-against-shared-mistakes.md)). Evidence: [referee report](../docs/evals/2026-11-15-access-held-out-a1-referee.md) (0 disagreements; rulings section), [`test_the_logged_table_matches_the_live_comparison`](../dataset/tests/test_access_referee_held_out.py).
- [x] The package is a workspace member with its tests in CI. Evidence: [`test_both_packages_import`](../tests/test_workspace.py); `access/tests` is in the CI test paths.

## 3. Policy YAML and desired state

Policy as code and the code that turns HRIS records into what each person should have.

**Done when:**

- [x] `access/policy/` holds roles, apps, birthright apps, exceptions (each with an expiry and an approval reference), the break-glass allowlist and settings (5 people per day, 50 accesses per day), validated against a schema in CI. Evidence: [`test_files_load_and_validate`](tests/test_policy.py); [#20](https://github.com/kstarev/meridian-it-lab/pull/20).
- [x] The roles, birthright and app lists in `access/policy/` are built from the public access policy table in `dataset/ACCESS_RECIPE.md` (the one the generator uses), and a test fails if the two drift apart. Evidence: [`test_roles_yaml_equals_the_public_markdown_table`](tests/test_policy.py).
- [x] Desired state for every tuning-world person is built from HRIS + policy + the reviewed title map, deterministically. Evidence: [`test_deterministic_and_golden`](tests/test_desired.py).
- [x] Contractors past their end date, leavers and people on leave get the expected desired state, checked against the tuning answer key. Evidence: [`test_contractor_past_end`](tests/test_desired.py), [`test_leaver_findings`](tests/test_desired.py), [`test_leave_of_absence`](tests/test_desired.py).
- [x] An unmapped job title gets birthright apps only, and is listed for a decision. Evidence: [`test_unmapped_title_gets_birthright_only`](tests/test_desired.py).
- [x] Expired exceptions stop granting access, checked by a test with a fixed clock. Evidence: [`test_exceptions_expire`](tests/test_desired.py).
- [x] `.github/CODEOWNERS` covers `access/policy/`. Evidence: [`test_codeowners_cover_the_workflows_and_the_plan_directory`](tests/test_apply_workflows.py).

## 4. Collectors and identity matching

Reading actual state from the three mocks and the 95 exports, and matching every account to a person.

**Done when:**

- [x] The Okta, Google and Slack mocks pass contract tests against recorded response shapes, including paging, `429` with `Retry-After` and transient `5xx` ([AAC-003](docs/adr/003-mock-api-shape.md)). Evidence: [`test_okta_users`](tests/test_contract.py) (and Google, Slack), [`test_faults_are_429_with_retry_after_or_503_and_seeded`](tests/test_mocks.py), [`test_429_waits_for_retry_after`](tests/test_collect.py); [#21](https://github.com/kstarev/meridian-it-lab/pull/21).
- [x] Collectors hold read-only tokens, and a test shows a write with one fails. Evidence: [`test_a_collector_token_cannot_write`](tests/test_collect.py).
- [x] All 95 exports parse: every column layout and date format, with a report of rows that can't be read (none silently dropped). Evidence: [`test_a_bad_row_is_listed_never_dropped`](tests/test_exports.py), [`test_every_line_is_accounted_for`](tests/test_exports.py).
- [x] Rules-only matching is scored on the tuning world: precision and recall on account-to-person pairs, with intervals. Evidence: [matching report](../docs/evals/2026-11-15-access-tuning-matching.md), [`test_rules_only_precision_and_recall_on_the_tuning_world`](tests/test_match.py).
- [x] The AI's title and match suggestions run through the spend ledger and cap ($3 and `ACCESS_CLAUDE_SPEND_CAP_USD` then; the per-item caps of lab ADR-037 since 2026-11-15), with structured outputs validated by code; a refusal or malformed answer counts as no suggestion. Evidence: [`test_a_call_that_would_cross_the_cap_is_not_made`](tests/test_ai.py), [`test_every_failure_is_no_suggestion`](tests/test_ai.py).
- [x] Haiku 5.5, Sonnet 5.5 and Opus 5.5 are compared on the tuning world only, in a GitHub Actions run approved by the owner; the report shows accuracy, precision, recall and cost, and the user picks a model ([AAC-006](docs/adr/006-model-choice-and-spend-cap.md)). Evidence: [the k=3 report](../docs/evals/2026-11-15-access-model-comparison-k3.md); the owner picked Haiku 5.5 on 2026-11-15.
- [x] The chosen prompts are frozen by hash. Evidence: [`test_the_prompts_are_frozen_by_hash`](tests/test_final.py), pins and freeze digest in [`ai/frozen.py`](src/meridian_access/ai/frozen.py) ([AAC-013](docs/adr/013-m7-frozen-prompts-and-the-owners-answers.md), [AAC-014](docs/adr/014-line-breaks-are-flattened-before-any-prompt.md)); the digest was confirmed by the owner as M7-R2 on 2026-11-16 ([m7-prep.md](docs/m7-prep.md), D12).

## 5. Diff, plan pull request and the mock Slack message

The diff, `plan.json` and `PLAN.md`, the rolling plan pull request and the mock Slack message ([AAC-002](docs/adr/002-the-plan-is-a-pull-request.md), [AAC-008](docs/adr/008-runs-on-github-only.md)).

**Done when:**

- [x] The diff finds every planted problem type on the tuning world, with precision and recall per type reported. Evidence: [plan report](../docs/evals/2026-11-15-access-tuning-plan.md), [`test_scores_per_type_on_the_tuning_world`](tests/test_plan.py); [#22](https://github.com/kstarev/meridian-it-lab/pull/22).
- [x] `plan.json` records the actual-state hash, and the same inputs give byte-identical plans. Evidence: [`test_same_inputs_give_byte_identical_files`](tests/test_plan.py).
- [ ] A leaver's full deprovisioning plan is ready in under 5 minutes from the HRIS change, measured in the workflow.
- [x] Changes resting on unreviewed AI suggestions never appear in a plan; they appear only as suggested map entries (a test). Evidence: [`test_no_unreviewed_suggestion_reaches_desired_state`](tests/test_ai.py).
- [x] The summary's names, apps and numbers all match `plan.json`, or the template summary is used (a test with a mocked model). Evidence: [`test_a_failed_check_or_no_answer_gives_the_template`](tests/test_summary.py).
- [ ] The user has created the GitHub App, the ruleset on `main` (pull request, code-owner review, checks, stale approvals dismissed) and the secrets (the App key and the Anthropic key).
- [ ] The scheduled workflow opens, updates and closes the one rolling plan pull request on the tuning world's simulated month, with the summary in the description and a comment each time the plan changes.
- [ ] The mock Slack adapter builds the Block Kit message (header, counts, warnings, pull request link), writes it to `out/access/slack-message.json`, and the workflow uploads it as an artifact.
- [x] Tests check the message: valid Block Kit structure, text within Slack's length limits, every number matching `plan.json`, nothing beyond what the pull request shows. Evidence: [`test_the_message_is_valid_block_kit`](tests/test_slack_mock.py), [`test_every_number_in_the_message_is_in_the_plan`](tests/test_slack_mock.py).
- [x] Nothing in Access-as-Code sends to Slack: the adapter has no network code, there is no Slack token or webhook anywhere, and a test fails if one appears. Evidence: [`test_the_module_imports_no_network_library`](tests/test_slack_mock.py), [`test_no_tracked_file_has_a_slack_token_or_webhook`](tests/test_plan_workflow.py).
- [x] The workflows pass the lab's baseline: actions pinned to SHAs, `permissions: {}` with per-job grants, no stored checkout credentials. Evidence: [`test_every_action_is_pinned_to_a_full_sha`](tests/test_plan_workflow.py), [`test_every_action_is_pinned_to_a_full_sha_and_checkout_keeps_no_credentials`](tests/test_guards.py), [`test_both_workflows_pin_every_action_to_a_sha_ci_already_uses`](tests/test_apply_workflows.py).

## 6. Apply, drift and blast radius

Merging the plan applies it to the mocks, safely ([AAC-007](docs/adr/007-safety-rails.md)).

**Done when:**

- [x] Apply is a dry run unless `ACCESS_APPLY_MODE=mock-live`, and there is no mode for real systems. Evidence: [`test_the_accepted_modes_are_exactly_dry_run_and_mock_live`](tests/test_apply.py); [#23](https://github.com/kstarev/meridian-it-lab/pull/23).
- [x] Applying the same plan twice changes nothing the second time, including after injected `429` and `5xx` errors. Evidence: [`test_applying_the_same_plan_twice_changes_nothing_the_second_time`](tests/test_apply.py), [`test_retries_after_429_and_5xx_give_the_same_final_state`](tests/test_apply.py).
- [x] A stale plan (actual state changed since it was built) is refused, and the next plan is rebuilt. Evidence: [`test_a_stale_plan_is_refused_and_nothing_changes`](tests/test_apply.py).
- [ ] The plan is split per cause-day: a day with 5 or more people or 50 or more removals (and the no-date bucket, and every bulk import) goes to a held plan that needs two approvals and can't be applied with one: the required check fails, and apply checks again; the rest is the main plan with one approval. The planted mass termination is caught this way.
- [x] No code path changes a break-glass account (a test over every change type). Evidence: [`test_no_change_type_ever_touches_a_break_glass_account`](tests/test_apply.py).
- [ ] Planted drift is flagged in the plan pull request and the mock Slack message, scored by precision and recall.
- [x] Non-SSO removals become manual tasks, confirmed against the next export. Evidence: [`test_export_flags_become_manual_tasks_and_not_api_calls`](tests/test_apply.py), [`test_manual_tasks_are_done_when_the_row_is_gone_from_the_next_export`](tests/test_apply.py).
- [x] N is reviewed against the tuning data, and changed by a policy pull request if needed. Evidence: [blast-radius report](../docs/evals/2026-11-15-access-blast-radius.md), [`test_the_results_doc_is_the_generated_report`](tests/test_blast.py); [#24](https://github.com/kstarev/meridian-it-lab/pull/24).

## 7. Results page

The sealed final score and a page that explains it. The prep is in [docs/m7-prep.md](docs/m7-prep.md): the frozen prompts, the final run and its dry check, the owner's rulings `M7-R1` to `M7-R17` and the run steps.

**Done when:**

- [x] The held-out world is scored once, with the frozen prompts and the chosen model, in an owner-approved GitHub Actions run. Done on 2026-11-17: [the run record](../docs/evals/2026-11-17-access-held-out-a1-run.md), counts and cost only.
- [ ] Every disagreement between the tool and the key has the owner's ruling in the rulings log before any number is published; key errors are reported separately and not counted against the tool ([AAC-010](docs/adr/010-human-rulings-before-publishing.md)).
- [ ] The report is published in `docs/evals/`.
- [ ] Precision and recall per problem type (target ≥ 0.90 each), identity matching rules only vs rules + AI, the safety counts (target 0) and cost, with intervals.
- [ ] `site/access/` is live at kstarev.dev/meridian-it-lab/access/, deployed by the existing Pages workflow.
- [ ] Every number on the page is checked against the published report by a test, as `test_site_numbers.py` does for tier0.
- [ ] The page states the honest limits from the brief and the compliance mapping as a mapping, not a claim.
- [ ] The held-out world becomes a dev set.

## What's left (2026-11-15, counts updated 2026-11-19)

41 of 54 boxes are ticked, each with its evidence (the milestone 4 freeze box on 2026-11-19). The 13 open ones:

- **Milestone 4:** all boxes are ticked. The summary's pass rate was not part of the model comparison and is not measured yet; it is not a box.
- **Milestone 5:**
  - The 5-minute leaver plan: `timing.json` and a local test time the whole plan build, but nothing measures it from an HRIS change in the workflow.
  - The owner's GitHub App, ruleset and secrets: the steps are in [docs/SETUP.md](docs/SETUP.md).
  - The scheduled workflow: `access-plan.yml` now runs nightly and opens, updates and closes both plan pull requests ([`test_plan_pr.py`](tests/test_plan_pr.py), [`test_plan_workflow.py`](tests/test_plan_workflow.py)). Tick it after the first green run with the App.
  - The mock Slack message is written to `out/access/plan/slack-message.json`, not `out/access/slack-message.json` as this box says, and its pull request link is still a placeholder: the plan is built before the pull request number is known.
- **Milestone 6:**
  - The held plan's required check blocks a merge only once the ruleset makes **Plan approvals** a required check.
  - Drift is in `PLAN.md` and the Slack message ([`test_drift_is_in_plan_md_and_the_slack_message_with_the_plan_json_count`](tests/test_apply.py)), but it hasn't been seen in a live plan pull request yet.
- **Milestone 7:** the final run happened on 2026-11-17, once, with the frozen prompts and Haiku 5.5 ([the run record](../docs/evals/2026-11-17-access-held-out-a1-run.md): counts and cost from the run's log). All four safety counts are 0. 214 disagreements wait for the owner's rulings ([AAC-010](docs/adr/010-human-rulings-before-publishing.md)); nothing is published before them. The report generator and the page are still to build ([docs/m7-prep.md](docs/m7-prep.md)).
