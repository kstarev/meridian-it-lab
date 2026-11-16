# Shadow AI Radar (oauth-radar): milestones

Each milestone is one branch and one pull request, merged by the repo owner. "Done when" lists the checks that must pass; each one is a test, a CI run or something a reviewer can see. Rules that hold in every milestone:

- Fake data only, `@meridian.example`, files shaped like exports, no network ([OAR-005](docs/adr/005-offline-export-shaped-files.md)).
- Read-only: the radar never revokes, blocks or changes a grant ([OAR-004](docs/adr/004-read-only-never-revoke.md)).
- Every existing world stays byte-identical: tier0's, Access-as-Code's, and the sealed ones ([OAR-003](docs/adr/003-grants-enter-the-shared-dataset.md)).
- `make lint` and the full test suite pass in CI, tier0's and Access-as-Code's tests included.
- Nothing is tuned on the held-out world, and the builder never reads its recipe ([OAR-006](docs/adr/006-practice-and-sealed-held-out-worlds.md)).
- No AI call, paid or local, and nothing in `budget/` changes, until milestone 4 has its approved budget item ([OAR-007](docs/adr/007-rules-decide-ai-explainer-later.md)).
- Every published number comes from a report and is checked by a test.

## 0. Brief, decisions and scaffold ([#46](https://github.com/kstarev/meridian-it-lab/pull/46))

The brief, decisions OAR-001 to OAR-007, this plan, and a small package: the data model, a loader for one export shape, two rules and a command line. No generated data, no AI.

**Done when:**

- [x] `oauth-radar/BRIEF.md`, `oauth-radar/MILESTONES.md` and `oauth-radar/docs/adr/` exist, and every decision is in the index with a date and a status. Evidence: [`test_every_adr_is_in_the_index`](tests/test_docs.py), [`test_every_adr_has_a_title_a_date_and_a_status`](tests/test_docs.py).
- [x] The lab brief points to this brief. Evidence: [`test_the_lab_brief_links_here`](tests/test_docs.py).
- [x] `meridian_oauth_radar` is a workspace member, and its tests are in the root test run and CI. Evidence: [`test_the_root_workspace_includes_this_package`](tests/test_package.py).
- [x] The data model has a grant and a finding. A grant has no field for content, and a finding can only ask for a review. Evidence: [`test_a_grant_has_no_field_for_content`](tests/test_model.py), [`test_a_finding_can_only_ask_for_review`](tests/test_model.py).
- [x] The loader reads a Google token export and IT's banned list. Fields it doesn't name never reach a grant, and a bad file or item stops the load with where it failed. Evidence: [`test_the_fixture_loads`](tests/test_load.py), [`test_extra_fields_never_reach_a_grant`](tests/test_load.py), [`test_a_bad_item_stops_the_load_and_says_where`](tests/test_load.py).
- [x] Two rules, `broad_scope` and `banned_app`, documented in the code and in [OAR-002](docs/adr/002-risk-model-rules-first.md), give a ranked list that doesn't depend on input order. Evidence: [`test_the_fixture_gives_four_ranked_findings`](tests/test_rules.py), [`test_the_same_grants_in_any_order_give_the_same_list`](tests/test_rules.py), [`test_every_rule_is_in_oar_002`](tests/test_docs.py).
- [x] `python -m meridian_oauth_radar` prints "milestone 0: nothing to scan yet", or scans the fake fixture, and leaves its input files unchanged. Evidence: [`test_no_file_says_nothing_to_scan_yet`](tests/test_cli.py), [`test_scanning_the_fixture_prints_the_ranked_findings`](tests/test_cli.py), [`test_the_scan_leaves_its_input_files_unchanged`](tests/test_cli.py).
- [x] The package imports no network or AI library, names nothing that revokes or deletes, and depends on nothing. Evidence: [`test_guards.py`](tests/test_guards.py).
- [x] No existing world moves: nothing in `dataset/` changes, and the tier0 and Access world tests pass unedited. Evidence: the pull request's diff; [`test_tier0_world_hash_unchanged`](../dataset/tests/test_tier0_worlds_unchanged.py), [`test_golden_manifest_hash`](../dataset/tests/test_access_world.py).
- [ ] The owner has reviewed the brief and OAR-001 to OAR-007, and answered the [questions in the brief](BRIEF.md#questions-for-the-owner). Each decision then moves from "Proposed" to "Accepted", or is changed.

## 1. The data

OAuth grants in the shared dataset: the practice world ([OAR-003](docs/adr/003-grants-enter-the-shared-dataset.md)).

**Needs first:** the owner's review of OAR-002 (it sets what to plant), OAR-003 (how grants enter the dataset) and OAR-005 (the file shapes).

**Done when:**

- [ ] `meridian_dataset.oauth` has its own generator version, seeds and recipe, and reads its people from the Access tuning world without writing to it.
- [ ] `make oauth-data` writes the practice world to `out/oauth-radar/`, offline, with no credentials.
- [ ] Same seed, same bytes: a golden hash test for the OAuth world.
- [ ] Every tier0 world and the Access tuning world keep their hashes, with their tests unedited, and a new test checks the Access world's hash before and after the OAuth world is built.
- [ ] The files follow OAR-005: Google tokens and app access, Slack integration logs and apps, people. A test fails if any file holds message content or a token value.
- [ ] The planted counts per problem type, and the decoys, match the recipe, checked from the answer key.
- [ ] The answer key is written apart, and a test fails if any loader input holds an answer-key field.
- [ ] Loaders for every file, so the practice world loads end to end, Slack included.

## 2. Rules-only detection with a scoring table

The full risk model of [OAR-002](docs/adr/002-risk-model-rules-first.md), the ranked list and the review ticket draft, tuned on the practice world.

**Needs first:** milestone 1's practice world; the owner's answers on the risk model.

**Done when:**

- [ ] `oauth-radar/policy/` holds the scoring table, the scope table, the banned-app list and the app catalogue, validated against a schema in CI. `.github/CODEOWNERS` covers it.
- [ ] Every signal and both overrides of OAR-002 are rules, each with a plain-English reason and a test.
- [ ] An unknown scope counts as broad, and an app missing from the catalogue gets the "not reviewed" points (tests).
- [ ] On the practice world: recall on high-risk grants is 100%, and precision and per-type numbers are reported with intervals, in a report in `docs/evals/` checked by a test.
- [ ] The review ticket draft: one per app, shaped like a Linear issue of type 9, every name and number in it matching the findings (a test). It's a file; nothing is filed.
- [ ] Same input, byte-identical findings and drafts.
- [ ] The rules, the scoring table and the catalogue are frozen by hash for milestone 3.

## 3. The held-out world

Scored once, on a world nobody tuned on ([OAR-006](docs/adr/006-practice-and-sealed-held-out-worlds.md)).

**Needs first:** milestone 2 frozen by hash; a blind helper to write the recipe; the owner's go for the final run.

**Done when:**

- [ ] A blind helper has written the held-out recipe; it's sealed by SHA-256 with count-only tests, and `--final-score=<its name>` is the only way to build it.
- [ ] The owner has decided whether a blind referee is needed, as Access-as-Code has, and if so it has run.
- [ ] The held-out world is scored once, with the frozen rules. Every disagreement with the key has the owner's ruling in the rulings log before any number is published, and key errors are reported apart.
- [ ] The report is in `docs/evals/`: recall (target 100%) and precision on high-risk grants, per type, with intervals; grants revoked, API calls and AI calls (target 0).
- [ ] The held-out world becomes a dev set.

## 4. Optional: an AI explainer

A short plain-English paragraph per app for the ticket draft. It never decides ([OAR-007](docs/adr/007-rules-decide-ai-explainer-later.md)).

**Needs first:** the owner wants it; a budget item for `oauth-radar` in a new budget decision (lab ADR-037 gives this project nothing); the owner's approval of that item in `budget/`; a choice of hosted or local model.

**Done when:**

- [ ] Every call goes through a spend ledger that refuses it without an approved item, as tier0's and Access-as-Code's do.
- [ ] The explainer sees only a finding's fields, never people's names.
- [ ] Every app name, scope and number in its text is in the finding, or the template text is used (a test with a fake model).
- [ ] It can't add, drop or re-rank a finding (a test).

## Not planned

- **Revoking grants.** It would need a new decision that supersedes OAR-004, with an approved plan, a dry run, a blast-radius limit and an allowlist.
- **A results page** on the project site. It can follow milestone 3, as Access-as-Code's does.
