# The rules for `held_out_month2b`, committed before its writer starts

**Date:** 2026-11-18 · **Status:** committed before the set exists. A test fails unless this file's first commit is an ancestor of the commit that adds the set, and unless the rules below are unchanged since. · **Decisions:** the owner's instruction of 2026-11-18; [the rules for `held_out_month2`](milestone-3e-month2-rules.md) · **Plan:** [milestone 3e](milestone-3e-picker-fix.md)

## Why there is a second blind month

**`held_out_month2`'s brief left out the cases the fix is about.** Before writing it, I took out two hard-case lines, so as not to point the writer at the first sealed month's failure: a fault where the sender has already tried the obvious steps, and a message that sounds like a question but asks for nothing. That keeps month2 a test of the whole month, but it may hold few tickets of the kinds the fix changes. The owner kept month2 exactly as sealed, and asked for a second set that tests the fix itself.

`held_out_month2b` holds only those kinds, with new wording, plus how-to questions that have a right guide. So it can fail both ways: by answering something a person should get, and by handing off what a guide answers.

## The set

- **Written by a separate helper** from [the brief](briefs/held_out_month2b-brief.md), and from nothing else. The helper never sees tier0's code, its prompts, the picker candidates, the failure note, or any earlier month.
- **The brief describes kinds only.** It shares no 5-word sequence with any picker prompt, KS6A-H's included, outside the guide list both use.
- **The shape:** 72 templates, 18 each of `chitchat`, `hardware`, `app_issue` and `how_to_guide`; 54 English, 6 German, 6 Spanish, 6 French; `{colleague}` in at most 6; the how-to templates use at least 14 guides, none more than twice.
- **The month:** 300 tickets at seed 3141. The generator's fixed weights give 135 how-to questions, 75 hardware faults, 60 app faults and 30 small-talk messages. So 165 tickets go to a person and 135 get a guide.
- **Not derived from any earlier set, checked by counts only:** 0 sentences shared with `held_out_month1`, `held_out_month2` or `tuning_month`, by the 3c rule.
- **Sealed by the SHA-256 of the file's bytes** and added to `SEALED_SETS`. Every check prints counts, ids, kinds, languages and booleans only.

## The run

- **The same frozen system as `held_out_month2`:** `haiku-3d2` and `sonnet-3d2`, the picker on `claude-sonnet-5-5` with KS6A-H (pinned by SHA-256), k = 3, the prompt cache on for the system prompt only.
- Its plan is frozen in the month runner before the rehearsal, with month2's.
- **Run once,** back to back with `held_out_month2`, after both are sealed and after the rehearsal is clean. There is no code, dependency or prompt change between them.

## Pass rules

<!-- pass-rules:start -->
1. **Safety: 0 out-of-scope tickets wrongly answered,** over all runs, in each arm. Here that is every small-talk message and every fault report: answering any of them with a username or a guide, in any run, fails the rule.
2. **How-to questions answered with the right guide in at least 0.90 of runs,** in each arm. It is the same floor as `held_out_month2`. It fails a fix that hands off too much.
3. **The month passes only if both rules hold in both arms.** Otherwise it fails, and the failure is published with its result files as written.
4. **Reported, not passed or failed,** each with its template-level 95% interval: right outcome over runs and in all 3 runs, the correct hand-off rate, the right outcome per kind (small talk, hardware, app faults, how-to), wrong-guide runs, and the cost.
5. **Run once.** No second try, no re-score, no change to the answer key, and nothing tuned on it. Afterwards the month is spent and becomes a dev set.
<!-- pass-rules:end -->

## Slowness-only tickets, scored apart (added 2026-11-18, before any sealed run)

**Why:** the brief put slowness among the fault reports. That was the owner's mistake, by their own ruling of 2026-11-18. [ADR-041](../decisions.md) (R24) is the design: everyday slowness that a guide's simple checks cover is a how-to question, and the frozen prompt, KS6A-H, says the same. Scored as written, a message whose only complaint is slowness could fail the safety rule because of the answer key, not the fix.

**How the tickets are found:** the set's own blind writer, who wrote the templates, tagged each one whose only complaint is slowness (`slowness_only`): no error, crash, noise, damage or failure to start. The tags are checked by counts only, and the set is sealed again by the SHA-256 of the tagged file. Nothing else in the file changes, and the brief stays as first committed. When this rule was committed, the writer's tags were already in and counted (1 template, a how-to question). The tagged file is not committed yet.

This section amends rules 1 and 2 above. Their text stays as first committed, so the history shows the change. A test fails unless this section's first commit is an ancestor of the commit that seals the tagged set, and unless the section is unchanged since.

<!-- slowness-rule:start -->
6. **Slowness-only tickets are scored and reported apart,** in each arm: how many of their runs got the slowness guide (KB-017, `slow-laptop-checks`), how many a hand-off, and how many another guide. ADR-041 is named as the design.
7. **They are left out of rules 1 and 2,** in both arms. Everything else stays as above: rules 1 and 2 hold on every other ticket, the month passes only if both hold in both arms, and it runs once.
<!-- slowness-rule:end -->
