# Mutation testing on Access-as-Code's safety code

**Date:** 2026-11-19 · **Cost:** $0: no model was called, and no sealed set was read · **Tool:** `tools/mutate.py` (from [the tier0 run](2026-11-18-safety-mutation-testing.md)) · **Results:** `out/mutation/access-before.json`, `out/mutation/access-after.json` (not tracked)

The same tool and the same changes as the tier0 run, on the four safety rails of [AAC-007](../../access/docs/adr/007-safety-rails.md): identity matching, the blast-radius limit, break-glass accounts and idempotent apply. A mutant is one file with one small change inside one named function; its tests are run against it, and a failing test kills it.

## What was mutated

| Target | File | Functions |
|---|---|---|
| access identity matching | `access/src/meridian_access/match.py` | `fold`, `norm_email`, `_split_number`, `_letters`, `username_templates`, `_Person.*`, `_name_key`, `_email_number`, `_Index.__init__`, `_unique`, `_names_agree`, `_by_email`, `match_accounts`, `_company_email`, `_apply_rules` |
| access blast radius | `access/src/meridian_access/limits.py` | `_is_iso_date`, `bucket_key`, `buckets`, `over_limit` |
| access blast radius | `access/src/meridian_access/approvals.py` | `required_for`, `count_approvals`, `check` |
| access blast radius | `access/src/meridian_access/plan.py` | `split_plan` |
| access break-glass | `access/src/meridian_access/policy/load.py` | `Policy.break_glass_kind`, `Policy.is_break_glass` |
| access break-glass | `access/src/meridian_access/match.py` | `_break_glass` |
| access break-glass | `access/src/meridian_access/diff.py` | `_break_glass_strings`, `_touches_break_glass` |
| access break-glass | `access/src/meridian_access/apply.py` | `_break_glass_names`, `_is_listed`, `_touches_break_glass` |
| access idempotent apply | `access/src/meridian_access/apply.py` | `normalise_mode`, `apply_plan`, `_verify`, `_took_effect`, `_one`, `_defer_delete`, `_send`, `_assign`, `_plan_call`, `_step` |

Each file's mutants run against the tests listed for it in `tools/mutate.py` (`UNITS`): before, the Access tests on main that cover it; after, those plus the new tests.

One change to the tool: a mutant's temporary folder now mirrors `<package>/src` and links the package folder's other entries beside it. `policy/load.py` finds `access/policy` by walking up from its own file, and its clean run failed without this. tier0's mutants are unchanged by it.

## Results

Score = killed / (mutants − survivors listed as equivalent or harmless in `tools/mutate_equivalent.json`, the same list for both runs).

Before (the tests on main, 832 s):

| Target | Mutants | Killed | Survived | Timed out | Equivalent or harmless | Score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| access identity matching | 172 | 144 | 28 | 0 | 16 | 92.3% |
| access blast radius | 118 | 108 | 10 | 0 | 3 | 93.9% |
| access break-glass | 58 | 43 | 15 | 0 | 4 | 79.6% |
| access idempotent apply | 230 | 183 | 47 | 0 | 12 | 83.9% |
| Total | 578 | 478 | 100 | 0 | 35 | 88.0% |

After (with the new tests, 865 s):

| Target | Mutants | Killed | Survived | Timed out | Equivalent or harmless | Score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| access identity matching | 172 | 156 | 16 | 0 | 16 | 100.0% |
| access blast radius | 118 | 115 | 3 | 0 | 3 | 100.0% |
| access break-glass | 58 | 54 | 4 | 0 | 4 | 100.0% |
| access idempotent apply | 230 | 218 | 12 | 0 | 12 | 100.0% |
| Total | 578 | 543 | 35 | 0 | 35 | 100.0% |

Killed / mutants: 82.7% before, 93.9% after. Each of the 35 left is listed below with its reason.

## The 65 survivors that mattered, and what kills them

| Where | Change | Killed by |
|---|---|---|
| `match.py` `_split_number` | a username with no number counts as number 1 | `test_match_properties.py::test_a_username_with_a_1_does_not_match_a_person_whose_email_has_no_number` |
| `match.py` `_names_agree` (4) | the row's family name ignored, or any one letter taken as an initial | `test_match_properties.py::test_the_row_names_must_agree_with_the_one_person_a_username_points_to` |
| `match.py` `match_accounts` | an account listed twice is listed twice | `test_match_properties.py::test_an_account_listed_twice_is_listed_once` |
| `match.py` `_apply_rules` (6) | rule 3 (Google aliases) needs both an exact and a normalised hit, or stops after a miss | `test_match_properties.py::test_a_google_alias_with_a_tag_or_in_capitals_still_places_the_account`, `test_an_address_that_is_another_google_user_s_alias_finds_that_user_s_person` |
| `limits.py` `_is_iso_date` | the `YYYY-MM-DD` check removed: `20261103` and `2026-W45-2` become cause days | `test_blast_radius_properties.py::test_other_iso_8601_forms_of_a_day_are_refused` |
| `approvals.py` `count_approvals` (3) | reviews no longer ordered by time; a review with no user crashes | `test_blast_radius_properties.py::test_only_a_code_owner_s_latest_approval_on_the_head_counts_never_the_author`, `test_a_later_change_request_listed_first_still_wins`, `test_a_review_with_no_user_is_skipped` |
| `plan.py` `split_plan` (3) | the checks for an unknown hold kind, a main part needing two approvals and a held part needing one removed | `test_blast_radius_properties.py::test_the_split_refuses_*` (3 tests) |
| `match.py` `_break_glass` (2) | a bare name the policy lists is lost; no kind returns `""` | `test_break_glass_properties.py::test_a_bare_name_the_policy_lists_as_written_is_protected_as_written`, `test_matching_leaves_an_account_with_no_protected_name_alone` |
| `diff.py` `_touches_break_glass` (5) | the diff's last word stops checking the policy itself: a change naming a listed account by a form no account in the actual state has is kept | `test_break_glass_properties.py::test_both_guards_stop_every_spelling_of_every_protected_name` |
| `apply.py` `_break_glass_names`, `_is_listed`, `_touches_break_glass` (4) | empty names collected; the bare-username form or the policy check dropped; every empty-account change dropped | `test_break_glass_properties.py` (names, spellings, ordinary accounts) |
| `apply.py` `apply_plan` (10) | mock-live with no tokens, the bad-data count and its log line, an exports folder overridden by the world's, the Okta app list read for every change, a refusal that returns nothing, the dry-run wording | `test_apply_properties.py` focused tests |
| `apply.py` `_verify`, `_took_effect` (3) | a failed re-read or a vanished account counted as applied | `test_when_the_state_cannot_be_read_after_the_writes_every_change_is_failed`, `test_an_account_gone_after_a_200_is_failed_not_applied` |
| `apply.py` `_one` (5) | an unknown action sent to the API; later changes not seeing what earlier ones did | `test_an_unknown_action_on_a_directory_account_is_not_applied`, `test_later_changes_in_one_plan_see_what_earlier_ones_did` |
| `apply.py` `_send` (7) | a failed, 404 or non-2xx write treated as done; 199, 300 and 301 taken as success or 299 as failure | `test_a_write_that_keeps_failing_*`, `test_a_404_for_a_gone_google_user_*`, `test_only_a_2xx_answer_is_a_success` |
| `apply.py` `_assign` (6) | adds made for non-Okta systems, deprovisioned users or unknown apps; a repeated add sent twice | `test_an_add_is_made_only_for_an_okta_app_a_live_okta_user_and_a_known_app`, `test_an_add_listed_twice_is_made_once` |
| `apply.py` `_plan_call` (2) | an already deprovisioned Okta user is deactivated again (a second apply writes), and `""` for "nothing to do" | `test_the_same_changes_again_change_nothing_and_write_nothing` |
| `apply.py` `_step` (2) | adds or Slack changes out of the documented order | `test_changes_run_suspend_first_then_app_removals_then_adds_then_the_rest`, `test_an_add_comes_after_an_app_removal_listed_after_it` |

## The 35 survivors left, with reasons

- 10. `access/src/meridian_access/match.py:97` `_split_number` (constant): `0` to `-1`. Harmless: with no digits the number becomes -1 instead of 0 on both sides (username and email), so they still agree; it differs only for a username or email ending in a bare 0, and the generator numbers repeated names from 2.
- 62. `access/src/meridian_access/match.py:219` `_names_agree` (constant): `None` to `''`. Equivalent: callers test the verdict with `is True` and `is False`, and "" is neither, as None is neither.
- 68. `access/src/meridian_access/match.py:258` `match_accounts` (and-or): `a.email or a.account` to `a.email and a.account`. Equivalent: only Google accounts carry aliases, and a Google account's login is its primary email, so `email and account` gives the same address.
- 69. `access/src/meridian_access/match.py:258` `match_accounts` (not): `a.email` to `not a.email`. Equivalent: only Google accounts carry aliases, and their email is never empty, so `not email or account` gives the login, which is the same address.
- 70. `access/src/meridian_access/match.py:258` `match_accounts` (not): `a.account` to `not a.account`. Equivalent: only Google accounts carry aliases, and their email is never empty, so the expression still gives the email.
- 91. `access/src/meridian_access/match.py:308` `_apply_rules` (constant): `0` to `-1`. Equivalent: `found` has exactly one person here, so found[-1] is found[0].
- 92. `access/src/meridian_access/match.py:309` `_apply_rules` (compare): `len(found) > 1` to `len(found) >= 1`. Equivalent: the length-1 case returned just above, so `>= 1` and `> 1` agree.
- 95. `access/src/meridian_access/match.py:309` `_apply_rules` (constant): `1` to `0`. Equivalent: the length-1 case returned just above, so `> 0` and `> 1` agree.
- 97. `access/src/meridian_access/match.py:311` `_apply_rules` (constant): `None` to `''`. Equivalent: callers only test `take`'s answer for truth, and "" is false like None.
- 128. `access/src/meridian_access/match.py:329` `_apply_rules` (constant): `None` to `''`. Equivalent: the caller tests the match for truth (`if found`), and "" is false like None.
- 148. `access/src/meridian_access/match.py:346` `_apply_rules` (constant): `None` to `''`. Equivalent: the caller tests the match for truth (`if found`), and "" is false like None.
- 151. `access/src/meridian_access/match.py:349` `_apply_rules` (compare): `len(candidates) > 1` to `len(candidates) >= 1`. Equivalent: the one-candidate case is handled by the branch above, so `>= 1` and `> 1` agree.
- 154. `access/src/meridian_access/match.py:349` `_apply_rules` (constant): `1` to `0`. Equivalent: the one-candidate case is handled by the branch above, so `> 0` and `> 1` agree.
- 163. `access/src/meridian_access/match.py:353` `_apply_rules` (constant): `0` to `-1`. Equivalent: `agree` has exactly one person here, so agree[-1] is agree[0].
- 165. `access/src/meridian_access/match.py:355` `_apply_rules` (constant): `None` to `''`. Equivalent: the caller tests the match for truth (`if found`), and "" is false like None.
- 172. `access/src/meridian_access/match.py:361` `_apply_rules` (constant): `None` to `''`. Equivalent: the caller tests the match for truth (`if found`), and "" is false like None.
- 175. `access/src/meridian_access/limits.py:38` `_is_iso_date` (return): `return False` to `return None`. Equivalent: the only caller uses `not _is_iso_date(...)`, and None is false like False.
- 177. `access/src/meridian_access/limits.py:42` `_is_iso_date` (return): `return False` to `return None`. Equivalent: the only caller uses `not _is_iso_date(...)`, and None is false like False.
- 278. `access/src/meridian_access/plan.py:289` `split_plan` (guard): `if sorted((_identity(c) for c in [*stay, *held_changes])) != expected: raise ValueError('the split lost or ...` to `(removed)`. Unreachable: `stay` and the held changes are built from one pass over the same lists, so the partition check cannot fail; it is a defensive check.
- 312. `access/src/meridian_access/diff.py:690` `_touches_break_glass` (guard): `if not account: return False` to `(removed)`. Equivalent: for an empty account the next checks are false too (no name is "", and neither "" nor "@meridian.example" is listed or matches the pattern).
- 314. `access/src/meridian_access/diff.py:691` `_touches_break_glass` (return): `return False` to `return None`. Equivalent: the caller uses `not _touches_break_glass(...)`, and None is false like False.
- 340. `access/src/meridian_access/apply.py:147` `_touches_break_glass` (guard): `if not account.strip(): return False` to `(removed)`. Equivalent: for an empty account the next checks are false too (empty names are left out, and neither "" nor "@meridian.example" is listed or matches the pattern).
- 342. `access/src/meridian_access/apply.py:148` `_touches_break_glass` (return): `return False` to `return None`. Equivalent: the caller tests the answer for truth, and None is false like False.
- 354. `access/src/meridian_access/apply.py:176` `apply_plan` (constant): `None` to `''`. Equivalent: policy_dir "" is passed on to build_plan and policy_sha256, which use `policy_dir or default_policy_dir()`, as for None.
- 403. `access/src/meridian_access/apply.py:284` `apply_plan` (and-or): `mode == MOCK_LIVE and write_token is not None` to `mode == MOCK_LIVE or write_token is not None`. Equivalent: in a dry run the clients this makes are never used (`_one` and `_assign` write only when not dry).
- 413. `access/src/meridian_access/apply.py:311` `apply_plan` (constant): `200` to `201`. Equivalent: the mock caps a page at 200, so a limit of 201 reads the same pages.
- 414. `access/src/meridian_access/apply.py:311` `apply_plan` (constant): `200` to `199`. Equivalent: with 199 per page the reader follows the next link, so every app is still read.
- 442. `access/src/meridian_access/apply.py:370` `_took_effect` (return): `return False` to `return None`. Equivalent: the caller tests the answer for truth, and None is false like False.
- 476. `access/src/meridian_access/apply.py:437` `_one` (constant): `None` to `''`. Equivalent: the default of `dict.pop` is thrown away.
- 481. `access/src/meridian_access/apply.py:466` `_send` (return): `return False` to `return None`. Equivalent: the caller uses `not _send(...)`, and None is false like False.
- 494. `access/src/meridian_access/apply.py:472` `_send` (return): `return False` to `return None`. Equivalent: the caller uses `not _send(...)`, and None is false like False.
- 506. `access/src/meridian_access/apply.py:476` `_send` (return): `return False` to `return None`. Equivalent: the caller uses `not _send(...)`, and None is false like False.
- 533. `access/src/meridian_access/apply.py:536` `_plan_call` (constant): `None` to `''`. Equivalent: the body of an Okta DELETE is ignored by the mock (and Okta takes none), so "" and None are the same call.
- 542. `access/src/meridian_access/apply.py:545` `_plan_call` (constant): `None` to `''`. Equivalent: an Okta lifecycle POST takes no body; the mock ignores it, so "" and None are the same call.
- 547. `access/src/meridian_access/apply.py:554` `_plan_call` (constant): `None` to `''`. Equivalent: an Okta lifecycle POST takes no body; the mock ignores it, so "" and None are the same call.

## What the new tests found in the code

No surviving mutant showed a bug. The property tests found one place where the code does not keep its rule. It is a test marked `xfail(strict=True)`; the source is unchanged.

1. **Rule 2 of identity matching ignores more than case, accents and a `+tag`.** `match.py:83`, `fold` runs NFKD and drops every character that is not ASCII, so a zero-width space, a soft hyphen, a Cyrillic or a CJK letter disappears. `ana.smith\u200b@meridian.example` is matched to the person `ana.smith@meridian.example` by `normalised_email`. An account under such an address is attributed to a real person, so it is never reported as an orphan. Pinned by `test_match_properties.py::test_rule_2_does_not_ignore_characters_other_than_accents` (4 cases).

Item 1 is gap G1. One more gap was pinned later the same day.

### G2: break-glass protection misses other names of a protected account

- **Rule:** the owner's ruling (2026-11-19): break-glass protection covers every name of a protected account, aliases included.
- **What happens:** a bare username counts only when the policy lists it or a pattern matches it.
  - `ops` is not protected when the Google user `ops@meridian.example` is protected through its listed alias.
  - Matching marks only the record that carries the protected name. An Okta user or Slack user under another name of the same account is matched as usual, or becomes an orphan to remove.
  - The policy compares names with `str.lower`, but matching uses `str.casefold`. So `opſ-root@meridian.example` (with a long s) is not the listed `Ops-Root@meridian.example`.
- **Why it matters:** apply deactivates the Slack user `ops` and deprovisions the Okta user `root-login@meridian.example`, though both are names of protected accounts.
- **Pinned:** the `test_g2_*` tests in `test_break_glass_properties.py` (12 cases, each a strict xfail). The source is unchanged.
- **Fixed** by [AAC-018](../../access/docs/adr/018-break-glass-covers-every-name-of-a-protected-account.md) (PR #129). The 12 tests now pass.
  - One module, `break_glass.py`, works out every name of a protected account, and matching, the diff and apply ask it.
  - The break-glass helpers named in the tables above (`match._break_glass`, `diff._break_glass_strings`, `diff._touches_break_glass`, `apply._break_glass_names`, `apply._is_listed`, `apply._touches_break_glass`) are gone. The break-glass target in `tools/mutate.py` now mutates `break_glass.py`.

### G7: break-glass protection spreads through a record the person can edit

- **Rule:** no plan changes a break-glass account, and a leaver's access is removed ([AAC-007](../../access/docs/adr/007-safety-rails.md)). Since AAC-018, every record that shares a name is one account, whoever can edit the record.
- **What happens:** a person can edit their own Slack profile email, and a Slack user can plant their userName ([red-team.md](../../access/docs/red-team.md)). A Slack user `ana.smith` with the email `breakglass-01@meridian.example` links `ana.smith`, `ana.smith@meridian.example` and the listed address into one protected account. So does a Slack userName `breakglass-01` with the email `ana.smith@meridian.example`.
  - Matching marks the leaver's Okta and Google users break-glass, so they are never placed on the leaver.
  - The diff's last word and apply's guard drop the suspension of `ana.smith@meridian.example`.
  - The plan has no warning. The never_touch rows for the leaver's own address say "listed in the break-glass policy", which it is not.
- **Why it matters:** one edit to a person's own Slack profile keeps their Okta and Google access after they leave. Found in review on 2026-11-20 (finding F2).
- **Pinned:** the `test_g7_*` tests in `test_break_glass_properties.py` (10 cases: 5 tests, each for the Slack email and the Slack userName, each a strict xfail). The source is unchanged.
- **Proposed fix, needs the owner's ruling:** the amendment at the end of [AAC-018](../../access/docs/adr/018-break-glass-covers-every-name-of-a-protected-account.md) (PR PRNUM). The 10 tests now pass.
  - Only records IT controls link names into one account: Okta users and their app assignments, and Google users.
  - A Slack user or an export row that carries a protected name is still left alone, and the guards also protect it by its key. It protects none of its other names, and the plan warns and names it.
  - Each never_touch row says how its protection was reached.

## Property tests

All use hypothesis with `derandomize=True` and `database=None`. Every person, account and plan is made up; apply runs on tiny in-process mocks.

`access/tests/test_match_properties.py`:
- Every account lands in exactly one of matched, unmatched and ambiguous, and the result does not depend on the order of the accounts or of the HR records.
- A match names a real person with its rule's confidence; a break-glass match names nobody.
- A break-glass account is never matched to a person, whether the protected name is its login, email or alias, in any case, padded or bare, even when it also carries a person's address; a near miss is matched as usual.
- The old record of a rehire never owns an account, and owns one when it is the only record.
- Two people with one name, one address or one username leave the account ambiguous; one person gets the match.
- An unknown company address is never placed by a display name, row names or a Slack username.
- A Slack account with an outside address needs agreeing names.
- A number in a username must agree with the email's.
- Case, accents and a `+tag` find the same one person.

`access/tests/test_blast_radius_properties.py`:
- Buckets count distinct people and removals per cause day (and one no-date bucket), checked against a count written in the test, with the limits varied.
- `over_limit` is true at or above either number.
- `required_for` is `approvals_over_n` exactly when a bucket is over or there is a block entry, in any order; more changes never need fewer approvals; adds, flags and report-only entries never count.
- At the people limit one day needs two approvals, one below needs one; removals with no person count toward removals only.
- A malformed or missing cause date is refused, never guessed.
- The split loses and duplicates nothing, main always needs one approval and holds nothing of an over-limit day, bad data stays in main's held list, and there is a held plan exactly when something must wait.
- Only a code owner's latest approval on the head commit counts, never the author's; the check needs the larger of the file's number and the rule's, two for a held plan, and the path of the plan's own part.

`access/tests/test_break_glass_properties.py`:
- The policy knows every spelling of a protected address (case, spaces, tabs); near misses are not protected; listed beats pattern.
- Matching marks a protected name wherever the account carries it, and leaves other accounts alone.
- The diff's last word and apply's guard both stop every spelling of every protected name, including names protected only through another name of the same account; neither stops an ordinary account.

`access/tests/test_apply_properties.py`:
- The same changes again change nothing and make no write.
- Order, repeats and injected 429 and 503 answers do not change the final state.
- A dry run changes nothing and lists exactly what a live run applies.
- Every change listed as applied or already done is in its wanted state afterwards.
- No change reaches a break-glass account, and without the break-glass policy the same changes would be made (the control).
- Too few approvals are refused before any write; a plan built before a state change is refused as stale, live or dry.

## What was not covered

- `diff.build_changes`: its break-glass filter is a function nested in a 100-line function whose other mutants are about holds. The diff's `_touches_break_glass` and `_break_glass_strings`, which the filter calls, are covered.
- `safety.py`: it counts the four safety numbers for milestone 7; it enforces nothing.
- Text constants (the break-glass pattern, the policy files) and the operators the tool lacks (see the tier0 report).

## How to re-run

```
uv run --locked python tools/mutate.py run --target "access " --baseline --out out/mutation/access-before.json
uv run --locked python tools/mutate.py run --target "access " --out out/mutation/access-after.json
uv run --locked python tools/mutate.py render out/mutation/access-before.json out/mutation/access-after.json
```

About 14 minutes each with 10 jobs on 12 cores. `make mutation` runs these targets and tier0's together. Not part of `make test` or CI.
