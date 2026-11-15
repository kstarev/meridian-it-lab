# Proposal for ruling R24: hand off broken hardware and app errors (not merged, not frozen)

**Date:** 2026-11-16 · **Status:** A measured proposal for the owner's ruling. No PR. The frozen milestone 4 picks stay KH2 (Haiku) and KS2 (Sonnet) unless the owner rules otherwise.

**Why:** the realistic month's tuning dry run (PR #47) posted a guide on a nearby topic for broken hardware and app errors: 21 of Haiku's runs, 7 of Sonnet's. ADR-035's never-answer list covers account actions and other people; incidents were added in milestone 4's plan. "Something is broken and needs a person" is on neither list.

**What this branch adds:** picker versions KH3 and KS3. Each is KH2 or KS2 plus one rule (`tier0/src/tier0/knowledge/prompts.py`): a report that something is broken or failing is not a how-to question when a person must look at it (damage, a spill, a dead or loose part, overheating, battery failure, a screen that stays black after the obvious checks, an app that crashes or shows an error). Everyday slowness, sound, Wi-Fi or VPN trouble that a guide covers stays a how-to question.

**Measured on tuning data only, k = 1** (optimistic: the rule was written from the month's misses):

| | Haiku KH2 | Haiku KH3 | Sonnet KS2 | Sonnet KS3 |
|---|---|---|---|---|
| Milestone 4 tuning: unsafe runs | 0 | 0 | 0 | 0 |
| Milestone 4 tuning: right guide (of 137) | 137 | 134 | 136 | 134 |
| Tuning month: out-of-scope wrongly answered (of 208) | 29 | 6 | 13 | 6 |
| Tuning month: hardware and app errors handed off (of 52) | 31 | 52 | 45 | 52 |
| Tuning month: how-to answered with the right guide (of 52) | 52 | 52 | 52 | 52 |

- The 6 left on the month are all messages with several questions (ruling R25), which no picker version can fix: triage sends them down the username path.
- **The cost:** on milestone 4's tuning set, KH3 and KS3 hand off "the second screen stays black when I plug in the dock" (2 runs), which the monitor guide answers. KS3 also hands off one keyboard order.
- **If the owner rules for this:** freeze KH3/KS3 in place of KH2/KS2 before the sealed how-to test runs, and add "hardware fault or app error" to ADR-035's never-answer list. Spent on this proposal: $1.31 (milestone 4) and $1.74 (3c).
