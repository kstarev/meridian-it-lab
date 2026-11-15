# Access tuning world: blast radius per day

**Date:** 2026-11-15 · **Decision:** [AAC-007](../../access/docs/adr/007-safety-rails.md)

How many removals and suspensions each simulated day's HR events cause on the tuning world (seed 6108), against the two-approver rule in `access/policy/settings.yaml`. The limit is counted per cause-day, not per plan: a day is over it when 5 or more people (`blast_radius_people`) have access removed because of that day's HR event, or 50 or more accesses (`blast_radius_removals`) are removed. Over-limit days go to a separate held plan that needs two code-owner approvals; the rest stays in the main plan, which needs one. Every bulk import is held too. The rule was set from these numbers (a reviewed policy change, 2026-11-15).

- Each removal or suspension in the plan (the held ones too, they are what the day would have caused) is attributed to the day of the HR event behind it: a terminate event for a leaver, a move for a mover, a leave start for a person on leave.
- A change with no HR event behind it (orphans, drift, people who left before the month) goes to a separate backlog bucket, not to any day; a contractor past their end date goes to a day only if a terminate event is behind it, else to the backlog. The backlog is shown separately, and the rule checks it as one extra bucket, "no date", against the same two numbers (the owner's choice). Every event day must be 0-29; anything else is an error.
- The median and p90 are over event days 1-29 excluding the bulk-import day (days with no removals count as zero).
- The attribution is the `cause_date` on each change, the same one the approval rule uses.

The table is generated: `uv run --locked python -m meridian_access.blast --world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-blast-radius.md` (`make access-data` makes `out/access/seed-6108`). A test fails if it drifts.

<!-- access-blast-radius:start -->
| Statistic | Removals and suspensions |
|---|---|
| Maximum event day | 475 |
| Median (p50), event days 1-29 excluding the bulk-import day | 0 |
| p90, event days 1-29 excluding the bulk-import day | 22 |
| Largest event day that is not the bulk import | 36 |
| Bulk-import day (day 28) | 475 |
| Backlog (orphans, drift, earlier leavers: no event day) | 96 |
| People with a backlog removal and an owner (the rule counts them as one day) | 18 |
| `blast_radius_people` in `settings.yaml` | 5 |
| `blast_radius_removals` in `settings.yaml` | 50 |

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

The limit is counted per cause-day: a day's bucket is over it when that day's HR event removes or suspends access for 5 or more people, or 50 or more accesses. Removals with no event day form one extra bucket, checked the same way. On the tuning data, 14 ordinary event days have removals:

| Leg | Ordinary event days needing two approvals | Bulk-import day needs two |
|---|---|---|
| People on one day, 5 or more | 0 | yes (30 people) |
| Removals on one day, 50 or more | 0 | yes (475 removals) |

The largest ordinary day has 36 removals and suspensions from 3 people; the most people on any ordinary day is 3. Counting people separates the bulk-import day from ordinary days; the removals number catches a few people with very many accesses.

On the whole-month tuning plan these buckets are over the limit and go to the held plan, a separate pull request: 2026-11-29 (30 people, 475 removals and suspensions); no date (18 people, 96 removals and suspensions). Every bulk import is held too. The held plan has 767 changes and needs 2 approvals; the main plan has 323 changes and needs 1.
<!-- access-blast-radius:end -->
