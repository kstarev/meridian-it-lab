# Access tuning world: blast radius per day

**Date:** 2026-11-15 · **Decision:** [AAC-007](../../access/docs/adr/007-safety-rails.md)

How many removals and suspensions each simulated day's HR events cause on the tuning world (seed 6108), against the limit N in `access/policy/settings.yaml`. A plan over N removals needs two code-owner approvals instead of one.

- Each removal or suspension in the plan (the held ones too, they are what the day would have caused) is attributed to the day of the HR event behind it: a terminate event for a leaver, a move for a mover, a leave start for a person on leave.
- A change with no HR event behind it (orphans, drift, contractors past their end date, people who left before the month) goes to a separate backlog bucket, not to any day. The backlog is a pile of old work, not a day's events, so it is shown but not used to judge N. Every event day must be 0-29; anything else is an error.
- The median and p90 are over event days 1-29 excluding the bulk-import day (days with no removals count as zero).
- N was not changed. The numbers below say it is too low for one ordinary day, and the proposal is for a policy pull request.

The table is generated: `uv run --locked python -m meridian_access.blast --world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-blast-radius.md` (`make access-data` makes `out/access/seed-6108`). A test fails if it drifts.

<!-- access-blast-radius:start -->
| Statistic | Removals and suspensions |
|---|---|
| Maximum event day | 475 |
| Median (p50), event days 1-29 excluding the bulk-import day | 0 |
| p90, event days 1-29 excluding the bulk-import day | 22 |
| Largest event day that is not the bulk import | 36 |
| Bulk-import day (day 28) | 475 |
| Backlog (orphans, drift, contractors past end, earlier leavers: no event day) | 96 |
| Limit N in `settings.yaml` | 25 |

| Day | Removals and suspensions | People |
|---|---|---|
| 4 | 4 | 1 |
| 5 | 16 | 1 |
| 6 | 3 | 1 |
| 7 | 15 | 1 |
| 8 | 2 | 1 |
| 9 | 22 | 2 |
| 10 | 9 | 2 |
| 12 | 25 | 2 |
| 13 | 21 | 3 |
| 14 | 7 | 1 |
| 16 | 36 | 3 |
| 20 | 22 | 2 |
| 24 | 3 | 1 |
| 26 | 11 | 2 |
| 28 | 475 | 30 |

N = 25 does not cleanly separate normal days from the bulk-import day: day(s) 16 are ordinary event days above 25 (the largest has 36, from only 3 people), while the bulk-import day has 475 from 30 people.

Proposal, for a policy pull request (settings.yaml is not changed here): count people as well as accesses, and require two approvals when one day's HR events touch 5 or more people (an ordinary day touched at most 3, the bulk-import day 30). The threshold is `MIN_BATCH_PEOPLE` in `diff.py`, the same constant the diff already uses before it holds a same-day batch of terminations, so there is one number for "how many people at once is a batch" and not two.

Raising N to 40 is not recommended: 3 ordinary leavers (about 48 removals at the ordinary average of 15.9 per leaver) already exceed it, so it would still hold up ordinary days, and a bulk change of a few people with few accesses each would get through.

This analysis assumes one plan per day. The current tuning plan covers the whole month at once: 292 removals and suspensions that are not held, so it needs 2 approvals regardless of any per-day rule.
<!-- access-blast-radius:end -->
