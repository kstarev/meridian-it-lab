# Milestone 7 prep: frozen prompts, the owner's rulings and the run steps

**Date:** 2026-11-15 · **Status:** Prepared overnight under the owner's "pick and log" rule. Nothing has run and nothing is paid. **Updated 2026-11-16:** M7-R16 decided as (a) and built, and the prompts re-frozen with a new digest (sections 1 and 5). M7-R14 decided as (a) and built: the four safety counts (sections 2, 5 and 7). **Updated 2026-11-17:** the final run happened, once, in the owner's approved run. Its counts and cost are in [the run record](../../docs/evals/2026-11-17-access-held-out-a1-run.md). The rulings come next (step 9), and nothing is published before them. Follows [AAC-005](adr/005-sealed-answer-key.md), [AAC-006](adr/006-model-choice-and-spend-cap.md), [AAC-010](adr/010-human-rulings-before-publishing.md), [AAC-011](adr/011-blind-referee-against-shared-mistakes.md) and lab [ADR-037](../../docs/decisions.md).

Milestone 7 scores the sealed held-out world once, with the chosen model (Haiku 5.5) and frozen prompts, in a GitHub Actions run the owner approves. Then the owner rules on every disagreement, and only then is anything published. This file covers:

1. what is frozen, and how to check it;
2. the final run and everything it refuses;
3. the dry check, which costs nothing;
4. the decisions made tonight, and why;
5. the rulings only the owner can give, `M7-R1` to `M7-R17`;
6. the run steps, in order, with the cost;
7. what is still to build.

The owner approved the budget item `aac-m7-final-score` on 2026-11-16 ([ADR-037](../../docs/decisions.md)), and the final run was made on 2026-11-17. It is never re-run.

## 1. What is frozen

The final run sends three kinds of call, all to `claude-haiku-5-5`:

- **title:** a role for a job title (`ai/titles.py`);
- **owner:** an owner for an account the rules left unmatched (`ai/matching.py`);
- **summary:** the plain-English paragraph of a plan (`summary.py`).

For each kind, four SHA-256 hashes are pinned in `access/src/meridian_access/ai/frozen.py`:

- **system:** the system prompt, hashed as its exact text (UTF-8 bytes, nothing added);
- **user:** the user message, built from fixed fake inputs (a probe title, two probe people, a one-change probe plan), so every fixed word of the template and every field it sends is covered. The fields code flattens carry line breaks of every kind (M7-R16), so the hash also covers the flattening;
- **schema:** the JSON schema sent as structured output, built from the same fake inputs;
- **request:** the whole request for those fake inputs: model, token limit (400, or 500 for a summary), thinking turned off, and the output format.

One more hash, the **freeze digest**, covers all twelve. It is the one number to confirm.

```text
owner.request    00e43b231b72961194636e35259d77959ee019cedf8542229b7c5f1dc9c5581e
owner.schema     e117eafd019103a826bed8d168c564e1fa5786fb24f03a20002ba165afb01f48
owner.system     f37b44fe503ec699b5ec8772efcd51e906ad21f0030e108c51da6ac554c5b6ec
owner.user       32a13cf03f555a4f31826691c9fd04130cb50285412202b775a4e66103988719
summary.request  6e905011f637da6f5fa271236ff3ed9bec64d0499e1f0f319f488a744dc36068
summary.schema   34ebde27d47aa44c7e7128cd27b29bb486b28f90959eac4209d20a86a4444e2e
summary.system   6ce3944b119b1ad0ff2a5bb801aa2555d0ececfa04b2ec96cbee0dc01e1a3fce
summary.user     0e917f55c55dc53e72a7d3e5fc060eb41493d2321c15fdc5b7d420a23b8c8658
title.request    649353fa7084ec1b827e634b10e0df0c091df5405c6309ad24ed691be53817ca
title.schema     4c3a72b74622c422779571ed407cbe7a76b1437fedec6be6db4cc17ceec4d7dc
title.system     5ec9d59ce662e983919f7d231e819019e5df304ff79f7a490b7324081597af69
title.user       9c10e87c5f173198b95ca8b8c6fc0e5d5a31ec13f5ee6c742252d3520210179a
freeze digest    e48df22324a83c66e69b6cd4f7d695a842e9fa75eba7c7da1c6cfe0c90eb24cf
```

**These are the prompts the model comparison measured.** The title and owner prompts are byte for byte the ones of commit `f4d6334`, the k=3 comparison ([report](../../docs/evals/2026-11-15-access-model-comparison-k3.md)), for any input without a line break. Since then only the default model line changed, and the M7-R16 fix. The summary prompt has never run live.

**Re-frozen on 2026-11-16, after the M7-R16 fix.** The first freeze (2026-11-15) had digest `f7f0d297…9005`. Code now replaces each run of line breaks in a field with one space before it builds a prompt (section 5, M7-R16). What changed, and what didn't:

- The six system and schema pins are unchanged.
- The six user and request pins changed only because the probe inputs now carry line breaks: `Probe\nJob\r\nTitle` is sent as `Probe Job Title`, and so on. Without the fix those probes would give other hashes, so removing the flattening, or any one kind of break from it, breaks the freeze. A test checks each.
- For input without a line break nothing moves. Tests check that each prompt builder gives the same bytes as before for such input. Checked once by script on 2026-11-16: the fixed code with the old probes gives the old twelve pins and `f7f0d297…9005`, and on the tuning world all 93 requests of the run are byte for byte the same with and without the fix.

**How it is enforced:**

- `test_the_prompts_are_frozen_by_hash` fails if any hash changes. Other tests change one piece at a time (a word of a prompt, a schema field, a token limit, the thinking setting, the fields sent about a candidate, the line-break flattening of each prompt, any one kind of break left out of it) and check that the freeze notices.
- The final run refuses to start if any hash differs, or if the chosen model (`CHOSEN_MODEL`) is not the frozen one.
- During the run, the frozen client refuses any other model, and any call whose system prompt or token limit isn't one of the three pinned ones. It refuses before the spend check, so nothing is sent.

**What the hashes don't cover:** the data inside each call (the titles, the candidates, the plan's lines), which comes from the world. What code does to that data before it is sent, such as flattening its line breaks, is covered through the probes. The role list comes from the policy, and a test keeps it equal to the public table. The rest of the code, such as which 20 candidates are shortlisted, is held by the commit the run uses. The Actions run records that commit.

**To check by hand:** `make access-data`, then `uv run --locked python -m meridian_access.ai final-check --world out/access/seed-6108`. A system prompt can also be checked alone: `uv run --locked python -c "import hashlib; from meridian_access.ai import titles; print(hashlib.sha256(titles.SYSTEM.encode()).hexdigest())"`.

## 2. The final run

`python -m meridian_access.ai final --final-score held_out_a1 --out DIR --k 3`, run only by the new workflow **Access final score** (`.github/workflows/access-ai-final.yml`). Code: `access/src/meridian_access/ai/final.py`.

**It refuses, in this order, before the sealed world is built:**

1. `--final-score` isn't exactly `held_out_a1`. This is the flag the dataset already uses ([AAC-005](adr/005-sealed-answer-key.md)); there is no second way to unseal.
2. A prompt hash or the model differs from the pins. The message names the parts, never the text.
3. `ACCESS_AI_LIVE` isn't `1`.
4. `ACCESS_CLAUDE_BUDGET_ITEM` is set to an item other than `aac-m7-final-score`. The run is charged to that item by code; the variable isn't needed.
5. `--out` is missing or inside the repository. The output holds held-out content, so it must never be committed by accident.
6. The budget item isn't approved, or the paid window (to 2026-12-17, UTC) has closed.

**Then, still before any paid call:**

7. It builds the held-out world on the runner.
8. It rehearses the whole run with a fake client: every call, the scoring and the output files, thrown away after. This finds the exact calls, and it makes a bug show up before money is spent.
9. It checks the worst case of all `k` repeats against the item's $8 cap, Access-as-Code's $30 cap and the run's limit. If it doesn't fit, it refuses. So a limit can never stop the run halfway and leave the held-out world half spent.

**Then it runs:** `k` repeats of every call, then the scores and every disagreement.

**What it writes,** to `$RUNNER_TEMP/access-final` (outside the checkout), uploaded as the `access-final-score` artifact for 30 days:

- `report.json`: the scores. These are plan precision and recall per finding type, matching for rules only and for rules plus AI, title accuracy (all titles, and titles the reviewed map doesn't cover), the AI's matches right and made, false matches repeated in every repeat, the summaries' pass or fail, calls, cost and the prompt hashes. Apart from the scores, it holds the four safety counts, each with its target of 0 (M7-R14). It is marked "unruled: not for publishing".
- `details.json`: every disagreement in full. This is held-out content.
- `rulings-template.md`: the rulings log, one row per disagreement, with the Ruling and Reason columns blank.

**What the log shows:** counts only: calls made and planned, cost, how many disagreements of each kind, and the four safety counts. No score, no name and no account. If anything fails after the start, the log gives only the error's type, because the message could quote held-out data.

**The disagreement lists** match exactly how the score counts, so each ruling maps to one change in a published number:

- `F001`, `F002`, …: plan findings. `missed` (in the key, not in the plan), `extra` (in the plan, not in the key), `differ` (same account, different type, action or owner).
- `M001`, …: account owners. For rules only and for each repeat of rules plus AI: `wrong person`, `false match` (the key says nobody) or `left unmatched`.
- `T001`, …: job titles, against the role most people with that title really hold.

## 3. The dry check (no money)

`python -m meridian_access.ai final-check --world out/access/seed-6108 --k 3`. It takes the tuning world only, and refuses any world whose seed isn't 6108 or whose recipe isn't `tuning`. It runs the final run's calls through the frozen client with a fake client that records each request and answers "none". Nothing leaves the process, no key is needed and no ledger is written. A test checks that its requests are exactly the ones the run sends. The workflow runs it first, as a free check.

Output on 2026-11-16, after the M7-R16 fix (the counts and costs are the same as on 2026-11-15; only the digest is new):

```text
dry check of the final score: no calls made, no key, no money. Tuning world only.
  model claude-haiku-5-5 at $0.10 in / $0.50 out per million tokens
  calls per repeat: 93 (76 title, 15 owner, 2 summary); k=3: 279
  upper cost: $0.0389 per repeat, $0.1168 for k=3
  prompt hashes (SHA-256): (the twelve above)
  freeze digest: e48df22324a83c66e69b6cd4f7d695a842e9fa75eba7c7da1c6cfe0c90eb24cf
  frozen: yes, every hash matches its pin
```

The upper cost is what the spend ledger checks before each call: the request's size in bytes plus 512 tokens in, and the full token limit out. Per call on the tuning world: title $0.00036, owner $0.00071, summary $0.00065.

## 4. Decisions made tonight

- **D1. Twelve hashes and one digest.** System prompts are hashed as plain text, so anyone can check them with one command. User messages and schemas are hashed from fixed fake inputs, because their real content is data. The whole request is hashed too, so a change of model, token limit or thinking setting also breaks the freeze.
- **D2. The run does what the comparison did, plus the two summaries.** It sends one title call per distinct title and one owner call per account the rules left unmatched, so the held-out numbers can be read next to the tuning comparison. ADR-037's estimate for the item includes "the two plan summaries", so they are in; the owner can drop them (M7-R3).
- **D3. The item is set by code.** The run always charges `aac-m7-final-score`, and refuses if the variable names another item. Nothing tonight set the budget variable anywhere.
- **D4. One way to unseal.** The run takes the dataset's own `--final-score held_out_a1` and builds the world itself, on the runner, only after its guards (as the referee does). Tests never build it.
- **D5. Rehearse, then check the whole cost, then call.** See section 2, steps 8 and 9.
- **D6. A $4.00 run limit.** That is over 30 times the tuning world's worst case at k=3, and half the item's $8 cap, which leaves room for one more run under M7-R8. The held-out world's size is sealed. If its worst case is bigger than $4.00, the run refuses before any call, and the limit can be raised by a pull request.
- **D7. No answer counts as a miss for titles.** The comparison left failed calls out of the count. It had none, so the numbers agree.
- **D8. Three disagreement lists**, matched exactly as the score counts (section 2).
- **D9. Output outside the checkout, counts only in the log,** error messages withheld.
- **D10. A new workflow,** `Access final score`. It is manual only, from `main` only, in the `access-ai` environment, and uses the same pinned actions as the compare workflow. It has a `k` choice of 3 or 1, a free freeze check first, and the spend ledger continued from the newest run of any of the three paid workflows (this one, the compare and, since R22, the red team). A test locks each of these.
- **D11. Small refactors, no change in behaviour:** the client's request is built by one function (`request_params`), the summary's token limit is a named constant, and `score_titles` shares its majority-role lookup with the new title disagreements. Every existing test passes unchanged.
- **D12. Boxes not ticked here.** The milestone 4 box "The chosen prompts are frozen by hash" and milestone 5's GitHub setup box are outside the lines this change may edit. The owner can tick them on merge. The evidence is `test_the_prompts_are_frozen_by_hash`, and M7-R7 for the setup.

## 5. The owner's rulings

Each ruling below gives what the owner decides, the options, a recommendation and why, and what happens on a "no". Answers before the run go in the table at the end of this file, in the same pull request that approves the budget item (M7-R1).

### Before the run

**M7-R1. Approve the budget item `aac-m7-final-score`.**

- **Decide:** whether to pay for the run. The item has an $8 cap and a $1.37 estimate (ADR-037).
- **Options:** approve as is; approve with a lower cap (the run refuses before any call if its worst case doesn't fit, and a cap below $4.00 also needs the workflow's run limit lowered, which a test checks); don't approve.
- **Recommendation:** approve as is, after M7-R2 to M7-R8, M7-R16 and M7-R17 are answered. Do it in its own pull request:
  - change `"approved": false` to `true` for that item in `budget/claude-credit-2026-12.json`;
  - add the item to `APPROVED` in `tests/test_budget.py`;
  - add one line to ADR-037's approval list, as for the tier0 items.

  Why: the expected spend is cents (section 6). The cap and the run limit are hard stops.
- **If no:** the run refuses before the world is built ("not approved yet"). Nothing is spent and the held-out world stays sealed. The paid window closes on 2026-12-17.

**M7-R2. Confirm the frozen prompts.**

- **Decide:** that the twelve hashes in section 1, freeze digest `e48df223…24cf`, are the prompts to score with. The prompts were re-frozen on 2026-11-16 after the M7-R16 fix, so this is the new digest, which the owner confirms. It replaces the first freeze's `f7f0d297…9005`.
- **Options:** confirm; or change a prompt first. A change means new pins in a reviewed pull request, and then the tuning comparison no longer describes the prompts. A new comparison would need the unapproved `aac-model-comparison` money.
- **Recommendation:** confirm. They are the prompts the k=3 comparison measured, and the pick was made on those numbers. The M7-R16 fix changes only fields that hold a line break, and on the tuning world none does: all 93 requests are the same bytes as before (section 1).
- **If no:** the run refuses until the code and the pins agree.

**M7-R3. Confirm what the run sends.**

- **Decide:** which calls are in the one run.
- **Options:**
  - (a) titles, owners and the two plan summaries, as built;
  - (b) titles and owners only, with `--no-summaries` added to the workflow by a pull request.
- **Recommendation:** (a). ADR-037 priced the summaries in. It is the only look at the summary on unseen data. It is reported as "passed n of 2", not as a rate, because two is far too few.
- **If no:** (b). The summary's pass rate stays unmeasured, and AAC-006 says it must be measured before the summary is turned on.

**M7-R4. Repeats, and which number is the headline.**

- **Decide:** `k` (the workflow asks for 3 or 1), and, for the numbers that depend on the model, which repeat is published as the headline. Those numbers are title accuracy, rules plus AI matching and the summaries. The plan's scores and rules-only matching don't use the model, so they are the same in every repeat.
- **Options:** k=1; or k=3 with the headline being the lowest of the three, or repeat 1, or the three pooled.
- **Recommendation:** k=3, with the lowest of the three repeats as the headline for each number, and all three shown.
  - Why k=3: M7-R5 is about a false match that repeats, and only repeats can tell one from noise, as on the tuning world.
  - Why the lowest: it can't be chosen after seeing the numbers.
  - The cost is three times k=1, still cents.
  - "Scored once" still holds: it is one run with three repeats, all reported, never best of three.
- **If no:** pick k=1 when dispatching. The false-match check then rests on one sample.

**M7-R5. How to judge Haiku's known limit: its one repeated false match.**

- **Context:** on the tuning world, Haiku gave 5 owners and one was wrong, the same in all three repeats. AAC-006 says that if Haiku makes false matches on the held-out world too, the pick is reopened. The report gives, per repeat, the AI's matches right and made, and `false_matches_in_every_repeat`.
- **Decide, before the run:** what counts as "false matches on the held-out world too", and what "reopened" means.
- **Options:**
  - (a) any false match the key confirms after the rulings, in any repeat;
  - (b) only a false match that repeats in every repeat;
  - (c) only if rules plus AI precision falls below the brief's 0.95.
- **Recommendation:** (a), the plain reading of AAC-006. A false match ruled "key wrong" under M7-R10 doesn't count. "Reopened" means:
  - the owner writes down a choice between keeping Haiku, with the limit stated on the results page, and moving later runs (nightly, `aac-red-team`) to Sonnet 5.5, which made no false match on tuning and costs about $0.22 a run;
  - the held-out score is never re-run and its numbers don't change (AAC-010);
  - the held-out false matches are on the results page either way.

  Why: AAC-006 already says so, and fixing the rule before the run means it can't be bent by the result.
- **If no:** choose (b) or (c) now, before the run, and write it in the table below.

**M7-R6. Where the raw results go.**

- **Context:** the repository is public. Anyone signed in to GitHub can read its Actions logs and download its artifacts. The log holds counts only. The `access-final-score` artifact holds the unruled numbers and the held-out details, kept 30 days.
- **Options:**
  - (a) as built: the artifact, 30 days;
  - (b) encrypt the artifact to a key only the owner holds. That needs a public key as a repository variable and one more step: a small pull request before the run, with no paid call;
  - (c) a shorter retention, such as 7 days.
- **Recommendation:** (a). The seal keeps the builder out, not the public: the sealed recipe file is already in the public repository. After the run the world becomes a dev set, and the rulings log publishes the disagreements anyway (AAC-010 asks for the item, what the tool did and what the key says). AAC-010's rule is about publishing numbers in a report or on the site, and the artifact says "unruled: not for publishing". Accepted risk: someone could read the raw numbers before the rulings are in.
- **If no:** (b) or (c), before the run.

**M7-R7. Confirm the GitHub setup the run needs.**

- **Context:** a read-only check on the night of 2026-11-15 (`gh api`, nothing changed) found:
  - environment `access-ai`: a required reviewer (the owner), deployments from `main` only, and the `ANTHROPIC_API_KEY` secret;
  - ruleset `main`, active: pull request, one approval, code-owner review, stale approvals dismissed, required checks **Lint and test** and **Plan approvals**, and a repository-admin bypass for pull requests only;
  - the GitHub App: variable `ACCESS_APP_ID` and secret `ACCESS_APP_PRIVATE_KEY`.

  `access/docs/SETUP.md` doesn't describe the `access-ai` environment. Only that environment blocks the final run. The ruleset is what makes merging the M7-R1 pull request mean an approval.
- **Decide:** that this is the intended setup. Then tick milestone 5's box "The user has created the GitHub App, the ruleset … and the secrets".
- **Options:** confirm; or change a setting first.
- **Recommendation:** confirm, and leave "prevent self-review" off on `access-ai`. The owner is the only reviewer, so turning it on would block every run. A later docs change can add the environment to SETUP.md.
- **If no:** fix the setting in GitHub before dispatching. Nothing tonight changed any setting.

**M7-R8. What if the run fails partway.**

- **Options:**
  - (a) a run that wrote no `report.json` is not the score: fix the bug in a pull request without opening held-out content, then run again, once;
  - (b) any started run is the score, even a failed one.
- **Recommendation:** (a). A crash gives no number that could be bent, the money at stake is cents, and the rehearsal makes a crash after the first paid call unlikely. Both runs stay in the ledger and the Actions history, and the report names the run that counts. A run that writes `report.json` is the score, whatever it says.
- **If no:** with (b), a failed run means `held_out_a1` can't be scored, and a final score needs a new sealed world (AAC-005).

### Before the run: two findings from the red-team mapping

These came from a red-team reading of the code on 2026-11-15, not from any held-out content. They were listed for the owner to decide. M7-R16 is now decided and built; M7-R17 is open.

**M7-R16. Line breaks inside HR fields in the line-based prompts.**

- **Decided on 2026-11-16: option (a), and built.** The owner's ruling: "Fix now. Strip or escape line breaks in code, then re-freeze the M7 prompts and give me the new digest." What was built:
  - one helper, `flatten_line_breaks` in `access/src/meridian_access/ai/prompt_text.py`. It replaces each run of line breaks with one space and changes nothing else. A line break is any boundary `str.splitlines()` splits on: LF, CR, CRLF (one break, so one space), VT, FF, FS, GS, RS, NEL, U+2028 and U+2029;
  - each prompt builder calls it on every field before it builds the prompt: `title_prompt` (title, department, team and role names), the summary's `change_line` and placeholder lines (action, account local part, app, reason; type names), and `match_prompt` (every string field of the account and of each candidate). So every path that builds a prompt gets it: the final run, the dry check, the comparison and the red-team harness;
  - the prompts were re-frozen with probe inputs that carry line breaks of every kind, so the freeze locks the fix (section 1). The recommendation below expected no pin to change, since the old probes had no line breaks; that holds for the old probes, but new probes were chosen on purpose so that the freeze covers the fix, so the six user and request pins changed. New freeze digest: `e48df223…24cf` (M7-R2);
  - tests (fake data only, `access/tests/test_line_breaks.py` and `test_final.py`): each kind of break in each field of each prompt forges no line; text without a break is unchanged byte for byte; removing the flattening from any prompt, or leaving any one kind of break out of it, breaks the freeze.

  What follows is the finding as it was written on 2026-11-15.

- **Context:** the title prompt is built line by line: `Job title: …`, `Department: …`, `Team: …`, `Roles: …`. The summary prompt is too: one line per change, built from the plan's fields. A line break inside a job title, department or team can therefore forge a line, such as a second `Roles:` line. The owner prompt is JSON, which escapes CR and LF, so it is mostly not affected. It keeps non-ASCII text as it is, so the Unicode line separators would pass through there too. The harm is bounded: a title answer must be one role from the schema's list, every suggestion is reviewed by a person, and code checks every word of a summary. But a forged line can still steer which role or which words the model picks.
- **Decide, before the run:** whether code removes or escapes line breaks in those fields before it builds the prompts.
- **Options:**
  - (a) replace every line break (CR, LF and the Unicode line and paragraph separators) with a space, in the title, department and team, in the plan fields that go into the summary's change lines, and in the owner prompt's name fields: a code-only pull request before the run;
  - (b) escape them, so the model sees a visible `\n` instead of a new line;
  - (c) leave the code as it is, and measure the effect with the red-team set (`aac-red-team`, planned for 12-12 to 12-13, after the run).
- **Recommendation:** (a). A line break carries no meaning in a job title, and a space keeps the words readable. It changes what the model sees only for a field that holds a line break. The frozen template text stays the same: the probe inputs have no line breaks, so the pins should not change, and the freeze test shows it either way. It has to be merged before the run, never after, because the run is scored once.
- **If no:** with (b), as (a) but the model sees the escape. With (c), the run sends such a field as it is, and the red-team set measures it later.

**M7-R17. The summary check stops echoed words, not false claims.**

- **Context:** the summary check accepts a paragraph only if every word is on a closed list and every number is a placeholder that code fills in. So planted words can't be echoed. But a false claim made only of allowed words passes, for example "approved", "nothing removed" or "no review needed". The final run's report counts a summary as "passed" when it passes this check. That says the words are allowed, not that the claims are true. The run doesn't save the two paragraphs, so the owner can't read them afterwards.
- **Decide, before the run:** whether to add a claims check first, or to measure the gap first with the red-team set.
- **Options:**
  - (a) add a claims check before the run, such as refusing a paragraph that says something is approved, not removed or needs no review when `plan.json` says otherwise: a code-only pull request;
  - (b) measure first with the red-team set, and in the final report call the result "passed the code check", with this limit stated;
  - (c) as (b), and also save the two paragraphs in `details.json` (a small code change before the run), so the owner reads them as part of M7-R10;
  - (d) drop the summaries from the run (M7-R3 (b)).
- **Recommendation:** (b). The model summary is off everywhere today: the nightly plan uses the template, and AAC-006 keeps it off until its pass rate is measured. A claims check written before measuring would be a guess at which claims matter, and the red-team set is built to find out. The final report states the limit plainly. Choose (c) as well if the owner wants to judge the two held-out paragraphs.
- **If no:** with (a), the claims check is merged before the run, and its tests run on the tuning world only. With (d), see M7-R3.

### At the run

**M7-R9. Approve the `access-ai` environment for the run.**

- **What happens:** after the owner dispatches **Access final score**, GitHub holds the job at "Waiting for review". The owner clicks **Review deployments**, ticks `access-ai` and clicks **Approve and deploy**. Only then does the job start and get the key.
- **Options:** approve; reject.
- **Recommendation:** approve only when M7-R1 to M7-R8, M7-R16 and M7-R17 are answered and merged. The job's first step then checks the freeze for free, and stops before the paid step if it fails.
- **If no:** the job doesn't run. Nothing is spent.

### After the run

**M7-R10. The rulings AAC-010 requires.**

- **What they are:** one ruling for every id in `rulings-template.md`: `tool wrong`, `key wrong` or `both acceptable`, with a one-line reason. The ids are plan findings (`F…`: missed, extra, differ), account owners (`M…`) and job titles (`T…`).
- **Who gives them:** the owner, nobody else (AAC-010).
- **In what form:** the template's table filled in: Id, Kind, Item, What the tool did, What the key says, Ruling, Reason. Key errors are listed again apart, with their count.
- **Where they are logged:** `docs/evals/<date>-access-held-out-a1-rulings.md`, in a pull request the owner merges. The format follows the practice-world rulings in the [referee report](../../docs/evals/2026-11-15-access-held-out-a1-referee.md).
- **The rule:** rulings never change the tool or lead to a re-run.
- **How many:** sealed until the run, and the log prints the counts. On the tuning world the same code lists 3 plan findings and 9 owners. Rules-only misses show up as owner rows.
- **Options:** rule on all three lists; or on plan findings only, AAC-010's three kinds, with owner and title numbers published marked "unruled".
- **Recommendation:** all three lists. Each one moves a published number, and a key error in owners or titles would bias those numbers the same way.
- **If no rulings:** nothing is published (AAC-010).

**M7-R11. How "both acceptable" counts.**

- **Context:** AAC-010 says a key error doesn't count against the tool, but doesn't say how "both acceptable" counts.
- **Options:** (a) as right for the tool, listed apart with its count, like key errors; (b) as wrong for the tool; (c) left out of both the count and the total.
- **Recommendation:** (a). An acceptable answer isn't a mistake, and listing them apart keeps them in view. Decide before the run.
- **If no:** the report code applies (b) or (c).

**M7-R12. One ruling for a group with the same cause.**

- **Options:** each id ruled one by one; or one ruling and reason applied to a list of ids with one cause, each id still with its own row.
- **Recommendation:** allow groups, with every id listed, as the practice-world rulings did (7 ids, one reason).
- **If no:** one by one, which takes longer.

**M7-R13. Who prepares the log.**

- **Options:** (a) the owner alone reads `details.json` and writes the log; (b) a helper (Claude) copies the template's rows into the log file and groups them by likely cause, leaving every Ruling and Reason blank.
- **Recommendation:** (b). The rows come from code anyway, and grouping speeds up M7-R12. By then the tool is frozen and the world is spent, so a helper seeing held-out content changes nothing that is scored. The helper never writes a ruling.
- **If no:** (a).

**M7-R14. The safety counts: before the run or after.**

- **Decided on 2026-11-16: option (a), and built in this pull request.** What was built:
  - `access/src/meridian_access/safety.py` counts the four from the plan, its two parts, the approvals each part has, what apply would send, the owner links with their rule, and the policy. No model is called.
  - `run_world` counts them before the first paid call, so a bug in them shows before money is spent. `report.json` holds them under `safety`, apart from the scores: each count with its target, 0, and how many changes it checked. The log prints the four counts only.
  - How the run counts them: both parts go to apply in a dry run on fresh mocks, with one approval each. The main part is merged with one, so apply would apply it. The held part needs two, so it is not merged, and apply must refuse it.
  - What each count is:
    - **break-glass:** a change, planned or applied, that names a break-glass account by any of its names. The report-only entry doesn't count.
    - **merged plan:** a change applied from a part without the approvals it needs, or not listed in that part.
    - **over the limit:** a removal or suspension applied from a part with fewer than two approvals, that is in a cause-day bucket over the limit or held by a bulk import. Buckets are counted on the whole plan, so a bucket split across both parts is still seen whole.
    - **unreviewed AI match:** a removal or suspension with an owner that no rule and no reviewed map entry links to that account.
  - On the tuning world all four are 0, and each one checked changes. Two are 0 by construction. Break-glass: matching never gives such an account an owner, the diff drops any row that names one, and apply drops it again. AI match: the plan is built before any call and never reads an answer. The other two are 0 because apply refuses the held part with one approval.
  - Tests (fake data only, `access/tests/test_safety.py`): 0 on the tuning world with a fake client; each count fails on a small case built to break it, and on the real plan made unsafe; the report and the log carry the four counts with target 0 and no name or email.

  What follows is the ruling as it was written on 2026-11-15.

- **Context:** the brief and AAC-005 report four safety counts, each with a target of 0: changes to break-glass accounts, changes without a merged plan, removals over the limit without two approvals, and removals that rest on an unreviewed AI match. No code counts them yet. They need no paid call.
- **Options:**
  - (a) build them in a code-only pull request before the run, so the one run writes every scored number;
  - (b) run first, then compute them on the same rebuilt world from the run's commit.
- **Recommendation:** (a). Nobody then writes measurement code after seeing held-out results, and a tool bug can't tempt a fix before publishing. It delays the run by one small pull request. ADR-037 planned the run for 11-21 to 11-28, and the window is open to 12-17.
- **If no:** (b). The report then says the safety counts were computed after the AI run, from the same commit's tool code.

**M7-R15. Publish, then retire the world.**

- **Decide:** merge the pull request that publishes the report (`docs/evals/<date>-access-held-out-a1.md`, the rulings log, and `site/access/` with its number test).
- **Then:**
  - `held_out_a1` becomes a dev set (AAC-005);
  - `aac-m7-final-score` goes back to unapproved, as the comparison item did;
  - the milestone 7 boxes are ticked.
- **Options:** publish; hold.
- **Recommendation:** publish only when every id has a ruling and the report's tests pass. Download the `access-final-score` artifact right after the run, because it expires after 30 days.
- **If no:** nothing is published, and the numbers stay in the downloaded artifact.

## 6. Run steps, once the rulings before the run are in

1. Answer M7-R2 to M7-R8, M7-R11 to M7-R14, M7-R16 and M7-R17 in the table below.
2. Merge any code-only pull request the answers call for: the safety counts (M7-R14 (a)), line breaks (M7-R16 (a), decided and built, with the new pins and digest for M7-R2), a claims check or saved paragraphs (M7-R17 (a) or (c)). Each one must keep `test_the_prompts_are_frozen_by_hash` green, or come with new pins and a new M7-R2 answer.
3. Open and merge the M7-R1 pull request: the budget item approved, `APPROVED` in `tests/test_budget.py`, the line in ADR-037, and the table below. Wait for **Lint and test** to pass.
4. Optional, free, on any machine: `make access-data`, then `uv run --locked python -m meridian_access.ai final-check --world out/access/seed-6108 --k 3`. Check that it ends with "frozen: yes" and digest `e48df223…24cf`.
5. Open `https://github.com/kstarev/meridian-it-lab/actions/workflows/access-ai-final.yml`, click **Run workflow**, keep **main**, choose **k** (3, per M7-R4), and click the green **Run workflow** button.
6. The job waits for review. Click **Review deployments**, tick `access-ai`, then **Approve and deploy** (M7-R9).
7. Watch the log:
   - **Check the prompts are still frozen** prints the digest and "frozen: yes".
   - **Score the held-out world** prints "frozen: yes … k=3", then "done: N calls of N planned, cost $…" and the disagreement counts.
   - If it prints "refusing: …", nothing was spent. Fix the cause and go back to step 5.
8. Download the `access-final-score` artifact straight away. Keep it outside the repository. The `access-ai-ledger` artifact holds the spend.
9. Rule (M7-R10 to M7-R13): fill in `rulings-template.md`, save it as `docs/evals/<date>-access-held-out-a1-rulings.md`, and merge it in a pull request.
10. Publish and retire the world (M7-R15).

**Cost.**

- **Upper bound for a world the size of the tuning world:** 93 calls per repeat, $0.0389 per repeat, $0.1168 at k=3.
- **Expected:** the comparison measured $0.0109 for Haiku's 91 calls, about $0.00012 a call. So a tuning-sized run is about $0.012 per repeat, $0.035 at k=3.
- **The held-out world's size is sealed.** New name spellings and username patterns may leave more accounts for the AI. Owner calls are the dearest, about $0.0007 each at most on the tuning world. At that size, the $4.00 run limit covers about 1,800 of them per repeat at k=3.
- **Hard stops:** the run limit ($4.00), the item's cap ($8; ADR-037 estimate $1.37) and Access-as-Code's $30. The run refuses before any call if its worst case doesn't fit.

## 7. Still to build (no paid call)

- **Before the run, M7-R14 (a): built in this pull request.** The four safety counts, added to `run_world` and its report, tested on the tuning world (section 5).
- **Before the run, if the owner chooses so:** the summary claims check or saved paragraphs (M7-R17). The line-break handling (M7-R16) is built.
- **After the run:** the report generator. It reads `report.json`, `details.json` and the rulings log, takes out key errors (and "both acceptable", per M7-R11), and writes `docs/evals/<date>-access-held-out-a1.md` with intervals, the key errors listed apart with their count, and a test that keeps the numbers in step.
- **After the run:** `site/access/`, the honest limits and the compliance mapping, with every number checked against the report by a test.
- **Not part of M7, still open in milestone 5:** the 5-minute leaver timing measured in the workflow.

## Owner's answers

Filled in from the owner's answers of 2026-11-16, recorded in [AAC-013](adr/013-m7-frozen-prompts-and-the-owners-answers.md), in the M7-R1 pull request. The owner confirms them by merging it.

| Ruling | Answer | Date |
|---|---|---|
| M7-R2 | Re-freeze after the M7-R16 fix (AAC-014). New digest `e48df22324a83c66e69b6cd4f7d695a842e9fa75eba7c7da1c6cfe0c90eb24cf`, merged in #54; merging this pull request confirms it | 2026-11-16 |
| M7-R3 | Send titles, owners and the two plan summaries; the summaries are reported as "passed n of 2" | 2026-11-16 |
| M7-R4 | k = 3, with the lowest repeat as each model-dependent headline, and all three shown | 2026-11-16 |
| M7-R5 | Any false match the key confirms after the rulings, in any repeat, reopens the Haiku pick (AAC-006). The held-out score is never re-run | 2026-11-16 |
| M7-R6 | Raw results stay in the run's artifact for 30 days, marked "unruled" | 2026-11-16 |
| M7-R7 | The GitHub setup as found on 2026-11-15, with "prevent self-review" left off on `access-ai` | 2026-11-16 |
| M7-R8 | A run that wrote no `report.json` is not the score. The bug is fixed without opening held-out content, and the run goes again once | 2026-11-16 |
| M7-R11 | "Both acceptable" counts as right for the tool, listed apart with its count | 2026-11-16 |
| M7-R12 | One ruling may cover a group with one cause, with every id still listed | 2026-11-16 |
| M7-R13 | A helper copies and groups the rows; it never writes a ruling | 2026-11-16 |
| M7-R14 | (a): the four safety counts are built in code before the run, in their own pull request | 2026-11-16 |
| M7-R16 | (a): fixed now; line breaks are flattened before any prompt (AAC-014, #54) | 2026-11-16 |
| M7-R17 | Measured first by the red-team run (AAC-015), then fixed. The report calls a passing summary "passed the code check" and states the limit | 2026-11-16 |
