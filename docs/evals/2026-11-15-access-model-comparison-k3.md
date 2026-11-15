# Access model comparison on the tuning world, k=3

**Date:** 2026-11-15 · **Decision:** [AAC-006](../../access/docs/adr/006-model-choice-and-spend-cap.md), [ADR-037](../decisions.md) · **Budget item:** `aac-model-comparison`

Haiku 5.5, Sonnet 5.5 and Opus 5.5 each did the same two jobs on the tuning world (seed 6108), three times over:

- **Job titles:** suggest a role for each of the 76 job titles the role map doesn't cover. Scored against the true roles in the answer key.
- **Leftover accounts:** suggest an owner, or "none", for each of the 15 accounts the matching rules left for a person. Scored as matching precision and recall for rules plus AI, over all 2019 accounts that have an owner. Rules only scores 2010/2010 precision and 2010/2019 recall ([tuning matching report](2026-11-15-access-tuning-matching.md)).

Source: "Access AI compare" run #2 ([43717990310](https://github.com/kstarev/meridian-it-lab/actions/runs/43717990310)), owner-approved, commit `f4d6334`. The run's report is saved next to this file as [2026-11-15-access-model-comparison-k3.json](2026-11-15-access-model-comparison-k3.json); the tables below are built from it. The report stores precision and recall as fractions; the counts are worked back from them and checked to match exactly. The answer key is used only to score. Nothing here touches held-out data.

## Scores

| Model | Job titles right (each repeat) | Matching precision, rules plus AI | Matching recall, rules plus AI | AI matches right / made | Cost, 3 repeats | Cost per repeat |
|---|---|---|---|---|---|---|
| Haiku 5.5 | 0.987 (75/76), 95% 0.929 to 0.998 | 0.9995 (2014/2015), 95% 0.9972 to 0.9999 | 0.9975 (2014/2019), 95% 0.9942 to 0.9989 | 4/5 | $0.0327 | $0.0109 |
| Sonnet 5.5 | 0.987 (75/76), 95% 0.929 to 0.998 | 1.0000 (2015/2015), 95% 0.9981 to 1.0000 | 0.9980 (2015/2019), 95% 0.9949 to 0.9992 | 5/5 | $0.6482 | $0.2161 |
| Opus 5.5 | 0.987 (75/76), 95% 0.929 to 0.998 | 1.0000 (2015/2015), 95% 0.9981 to 1.0000 | 0.9980 (2015/2019), 95% 0.9949 to 0.9992 | 5/5 | $1.3421 | $0.4474 |

| Model | Repeat | Calls | Failed or refused | Job titles right | Precision | Recall | Cost |
|---|---|---|---|---|---|---|---|
| Haiku 5.5 | 1 | 91 | 0 | 75/76 | 0.9995 | 0.9975 | $0.0109 |
| Haiku 5.5 | 2 | 91 | 0 | 75/76 | 0.9995 | 0.9975 | $0.0109 |
| Haiku 5.5 | 3 | 91 | 0 | 75/76 | 0.9995 | 0.9975 | $0.0109 |
| Sonnet 5.5 | 1 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.2155 |
| Sonnet 5.5 | 2 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.2160 |
| Sonnet 5.5 | 3 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.2167 |
| Opus 5.5 | 1 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.4473 |
| Opus 5.5 | 2 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.4466 |
| Opus 5.5 | 3 | 91 | 0 | 75/76 | 1.0000 | 0.9980 | $0.4481 |

- Every model ran 273 calls (91 per repeat) with none failed or refused, and none was stopped by a cap.
- **The three repeats of each model gave identical scores.** So the repeats add no information about run-to-run spread; they show the results are stable. The 95% intervals are Wilson intervals over the items scored (76 titles, or the accounts), not over repeats.
- The report doesn't record which title each model missed, so it can't say whether all three missed the same one.

## What separates the models

- **Job titles:** nothing. All three got 75 of 76 in every repeat.
- **Leftover accounts:** one account. Sonnet and Opus gave five accounts an owner and all five were right. Haiku gave five an owner and one was wrong, the same in all three repeats. All three left the other ten for a person.
- That one account is inside the intervals: Haiku's precision interval (0.9972 to 0.9999) overlaps Sonnet's and Opus's (0.9981 to 1.0000), and 4/5 against 5/5 is far too few to tell apart.

## Cost and spend against the cap

| | Amount |
|---|---|
| Spent by this run (ledger entries tagged `aac-model-comparison`, 819 calls) | $2.0229 |
| ADR-037 estimate for the item | $2.03 |
| The item's cap | $12.00 |
| Left under the cap | $9.98 |

- The ledger agrees with the report: Haiku $0.0327, Sonnet $0.6482, Opus $1.3421.
- Per repeat, Sonnet costs about 20 times Haiku, and Opus about 41 times.
- The first run (k=1, $0.677, run 43703011539) was before the budget. Its ledger entries carry no item, so it counts against no item's cap.
- The per-run limit was $4.00; the run used about half of it.

## Recommendation

**The models tie within the intervals, so the pick is the cheapest: Haiku 5.5.**

- Best score first: Sonnet and Opus are one account ahead of Haiku on matching and level on titles. That gap is inside the 95% intervals, so it is a tie, not a win.
- Then cost: Haiku does the whole job for about a cent per run.
- The caveat, plainly: Haiku's one miss is a false match, which this project treats as worse than no match, and it repeated in all three repeats, so it isn't a fluke of one run. It is still one account in 2015. Every AI suggestion is reviewed by a person before anything depends on it ([AAC-006](../../access/docs/adr/006-model-choice-and-spend-cap.md)), so the false match costs a reviewer one correction; it applies nothing by itself.
- If a false match should weigh more than this tie rule allows, Sonnet is the next cheapest with no false matches here, at about $0.22 per run.

The model config is unchanged. It changes only when the owner approves the pick.
