# Access tuning world: the plan, scored against the answer key

**Date:** 2026-11-15 · **Decision:** [AAC-002](../../access/docs/adr/002-the-plan-is-a-pull-request.md), [AAC-007](../../access/docs/adr/007-safety-rails.md)

The plan the diff builds for the tuning world (seed 6108): desired state from HRIS and policy, actual state from the three mocks and the 95 exports, rules-only matching, then the diff and the safety rails. The planner never reads the answer key. The key is used only here, to score.

- A change counts as right when it matches a finding on the target (system, app id, account without case or spaces), the type, the action and the employee id. A `hris` finding matches on its type and employee id only.
- **Precision** is the share of the plan's changes of a type that are in the key. **Recall** is the share of the key's findings of a type that the plan has.
- The accounts the matching rules can't place on purpose (people who changed their name, whose export row still has the old family name) are not guessed at. They show up as orphaned export rows to flag, which is where orphaned-account precision loses its few points: a person has to look at them, and the AI suggestions (reviewed first) are for them.
- The tuning world plants no HR data errors, so that row has nothing to measure (the rules are tested on made-up records).
- The rules were tuned on this world through `meridian_access.score` only, never for one person or account.

The table is generated: `uv run --locked python -m meridian_access.score plan --world out/access/seed-6108 --write-doc docs/evals/2026-11-15-access-tuning-plan.md` (`make access-data` makes `out/access/seed-6108`). A test fails if it drifts.

<!-- access-plan-score:start -->
| Finding type | Predicted | In the key | Precision | Recall |
|---|---|---|---|---|
| leaver_access_left | 141 | 141 | 1.000 (141/141), 95% 0.973 to 1.000 | 1.000 (141/141), 95% 0.973 to 1.000 |
| orphaned_account | 37 | 34 | 0.919 (34/37), 95% 0.787 to 0.972 | 1.000 (34/34), 95% 0.898 to 1.000 |
| contractor_past_end | 94 | 94 | 1.000 (94/94), 95% 0.961 to 1.000 | 1.000 (94/94), 95% 0.961 to 1.000 |
| leave_of_absence | 12 | 12 | 1.000 (12/12), 95% 0.757 to 1.000 | 1.000 (12/12), 95% 0.757 to 1.000 |
| mover_stale_access | 164 | 164 | 1.000 (164/164), 95% 0.977 to 1.000 | 1.000 (164/164), 95% 0.977 to 1.000 |
| rehire | 4 | 4 | 1.000 (4/4), 95% 0.510 to 1.000 | 1.000 (4/4), 95% 0.510 to 1.000 |
| drift | 6 | 6 | 1.000 (6/6), 95% 0.610 to 1.000 | 1.000 (6/6), 95% 0.610 to 1.000 |
| break_glass | 6 | 6 | 1.000 (6/6), 95% 0.610 to 1.000 | 1.000 (6/6), 95% 0.610 to 1.000 |
| hris_mass_termination | 1 | 1 | 1.000 (1/1), 95% 0.207 to 1.000 | 1.000 (1/1), 95% 0.207 to 1.000 |
| hr_data_error | 0 | 0 | n/a (none predicted) | n/a (none in the key) |
| all | 465 | 462 | 0.994 (462/465), 95% 0.981 to 0.998 | 1.000 (462/462), 95% 0.992 to 1.000 |
<!-- access-plan-score:end -->
