# Access final score on the sealed held-out world: the run record

**Date:** 2026-11-17 · **Decisions:** [AAC-010](../../access/docs/adr/010-human-rulings-before-publishing.md), [AAC-013](../../access/docs/adr/013-m7-frozen-prompts-and-the-owners-answers.md), [ADR-037](../decisions.md) · **Budget item:** `aac-m7-final-score`

**This is a run record, not the published score.** It holds only what the run's log printed: counts and cost. The run's artifact was not opened for it, and neither was the sealed world. The score itself (precision and recall per problem type, matching with rules only against rules + AI, title accuracy, their intervals and the key errors) stays unpublished until the owner has ruled on every disagreement ([AAC-010](../../access/docs/adr/010-human-rulings-before-publishing.md)).

Source: "Access final score" run #1 ([44080012770](https://github.com/kstarev/meridian-it-lab/actions/runs/44080012770)), dispatched by the owner behind the `access-ai` environment's approval, commit `48d11a5`, 2026-11-17 12:06 to 12:15 UTC. Its one job, "Score the held-out world once", ended green.

## What ran

| | |
|---|---|
| World | `held_out_a1`, sealed, scored once |
| Prompts | frozen: digest `e48df223…24cf`, and every hash matched its pin |
| Model | `claude-haiku-5-5`, the chosen model ([AAC-006](../../access/docs/adr/006-model-choice-and-spend-cap.md)) |
| Repeats | k = 3 |
| Calls | 309 of 309 planned |
| Cost | $0.0404 (worst case $0.1371) |

- **The run finished.** No spend limit stopped it and no error was printed.
- **The sealed world took 103 calls per repeat.** The free check before the paid step runs on the tuning world, which takes 93 (76 titles, 15 owners, 2 summaries). The log doesn't split the sealed world's 103 by job.
- **The ledger continued from the model comparison's** (run 43717990310), so the item's cap saw the earlier Access spend.

## Safety counts (target 0 each)

| Count | Result |
|---|---|
| Changes to break-glass accounts | 0 |
| Changes without a merged plan | 0 |
| Removals over the limit without two approvals | 0 |
| Removals resting on an unreviewed AI match | 0 |

All four meet their target (M7-R14 in [the prep](../../access/docs/m7-prep.md)).

## Disagreements waiting for the owner's ruling

| List | What one item is | Items |
|---|---|---|
| Findings | a finding where the plan and the key differ | 32 |
| Matches | an account in the key whose owner differs from what rules only, or rules + AI in any repeat, gave it | 179 |
| Titles | a job title where the model's role in any repeat differs from the key's | 3 |
| **All** | | **214** |

- **These are list lengths, as the log prints them.** An item is counted once, however many repeats it shows up in.
- **They are not a score.** The log doesn't say how many of them are the tool's mistakes, how many the key's, or how many accounts, findings and titles agreed. Precision and recall can't be worked out from this page.
- **Every one needs the owner's ruling before any score is published** (AAC-010, M7-R10). A ruling of "key wrong" takes the item out of the tool's errors, and "both acceptable" counts as right for the tool and is listed apart (M7-R11). Rulings never re-run anything.
- **179 is a large list to rule on by hand.** The log doesn't say whether the items share a cause. The details file does, and only the owner reads it.

## What the log doesn't hold

- Precision and recall per problem type, against the 0.90 target.
- Matching with rules only against rules + AI, and Haiku's matches right and made in each repeat. M7-R5 reopens the Haiku pick on any false match the key confirms, so that question is open until the rulings.
- Title accuracy, every interval, and the key errors.
- Whether both plan summaries passed the code check.

All of it is in `report.json` and `details.json` inside the run's `access-final-score` artifact, marked unruled (M7-R6).

## What happens next

1. **The owner downloads the artifact before it expires.** GitHub keeps it for 30 days, to 2026-12-17.
2. **The owner rules on each id** and merges the rulings log as `docs/evals/<date>-access-held-out-a1-rulings.md` (step 9 of the prep's run steps).
3. **The report generator and `site/access/` are still to build** (section 7 of the prep). The generator reads the report, the details and the rulings log, and writes the published report with its test.
4. **Then the world becomes a dev set** and `aac-m7-final-score` goes back to unapproved (M7-R15). Until then `held_out_a1` stays sealed and unread.

## Money

- The run cost **$0.0404 of the item's $8 cap.** ADR-037's estimate was $1.37, priced on the dearest model before Haiku was chosen.
- The held-out score is never re-run, so the item has no further planned spend.
