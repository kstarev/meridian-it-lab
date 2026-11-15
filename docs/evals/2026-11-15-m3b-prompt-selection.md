# Milestone 3b: prompt selection on the harder tuning set

**Date:** 2026-11-15 · **Decisions:** [ADR-033](../decisions.md), [ADR-030](../decisions.md), [ADR-031](../decisions.md) · **Budget item:** `tier0-3b`

**Every number on this page is a tuning score, and optimistic:** the prompts were written after seeing tuning misses, and each model keeps the best of up to 5 versions per prompt. Only held-out v4 and v4b measure performance (ADR-038).

All 116 tuning messages: the first 66 and the 50 of the harder tuning set (`dataset/src/meridian_dataset/pools/tuning_hard.json`). k = 3, so every count is out of 3 runs per message. The Claude API takes no temperature and no seed.

The rule is 3a's, unchanged: a version may not catch fewer colleague requests than version 1 on that model; among those, the most correct everyday answers wins; a tie goes to more colleague catches, then to the shorter prompt.

## Haiku 5.5: Triage

| Version | Everyday | Self-descriptions | Colleague requests | of which unnamed | Characters | Cost |
|---|---|---|---|---|---|---|
| T1 | 183/183 | 60/63 | 63/102 | 36/57 | 641 | $0.0172 |
| HT2 | 181/183 | 61/63 | 89/102 | 49/57 | 860 | $0.0194 |
| HT3 | 181/183 | 63/63 | 89/102 | 51/57 | 1177 | $0.0222 |
| HT4 **(picked)** | 183/183 | 63/63 | 75/102 | 45/57 | 1039 | $0.0209 |
| HT5 | 181/183 | 63/63 | 91/102 | 51/57 | 1403 | $0.0244 |

## Haiku 5.5: Own-account check

| Version | Everyday | Self-descriptions | Colleague requests | of which unnamed | Characters | Cost |
|---|---|---|---|---|---|---|
| C1 **(picked)** | 183/183 | 57/63 | 96/102 | 51/57 | 760 | $0.0185 |
| HC2 | 172/183 | 63/63 | 102/102 | 57/57 | 1176 | $0.0224 |
| HC3 | 180/183 | 63/63 | 96/102 | 51/57 | 1081 | $0.0217 |
| HC4 | 173/183 | 63/63 | 102/102 | 57/57 | 1326 | $0.0240 |
| HC5 | 180/183 | 63/63 | 99/102 | 54/57 | 1384 | $0.0245 |

## Sonnet 5.5: Triage

| Version | Everyday | Self-descriptions | Colleague requests | of which unnamed | Characters | Cost |
|---|---|---|---|---|---|---|
| T1 | 183/183 | 63/63 | 91/102 | 51/57 | 641 | $0.3616 |
| ST2 | 182/183 | 63/63 | 99/102 | 54/57 | 1005 | $0.4203 |
| ST3 | 183/183 | 63/63 | 95/102 | 54/57 | 867 | $0.4039 |
| ST4 **(picked)** | 183/183 | 63/63 | 101/102 | 56/57 | 1294 | $0.4800 |

## Sonnet 5.5: Own-account check

| Version | Everyday | Self-descriptions | Colleague requests | of which unnamed | Characters | Cost |
|---|---|---|---|---|---|---|
| C1 | 183/183 | 63/63 | 99/102 | 54/57 | 760 | $0.3814 |
| SC2 | 180/183 | 63/63 | 102/102 | 57/57 | 1005 | $0.4252 |
| SC3 | 183/183 | 63/63 | 99/102 | 54/57 | 910 | $0.4116 |
| SC4 **(picked)** | 183/183 | 63/63 | 102/102 | 57/57 | 1213 | $0.4672 |

## What was picked, and what the rule passed over

| Setup | Triage | Own-account check |
|---|---|---|
| `haiku-3b` | HT4 | C1 (unchanged) |
| `sonnet-3b` | ST4 | SC4 |

- **Sonnet** ends with no everyday answer wrong. Its triage misses one colleague-request run of 102 and its check none, so no tuning message gets past both stages.
- **Haiku's triage** still misses 27 of 102 colleague-request runs with HT4. HT5 misses 11, but gets 2 of 183 everyday runs wrong, so the rule passes it over. The rule puts everyday answers first; here that costs colleague catches.
- **Haiku's check** stays at version 1 for the same reason: HC2 and HC4 catch every colleague request but get 10 or 11 everyday runs wrong.
- The misses are requests relayed in someone's own words ("my report says: 'I need my login'"). When triage misreads one as the sender's own, the own-account check is the second line. With HT4 and C1 together, 2 of Haiku's 34 colleague requests get past both stages (listed in the JSON next to this file).
- A misread relayed request is answered with the sender's own username, not the other person's: a wrong answer, not a leak.

Scoring cost: $3.57 for these versions. The versions' text is in `tier0/src/tier0/evals/prompt_history.py`; the frozen setups are pinned by hash in `tier0/tests/test_setups.py`.
