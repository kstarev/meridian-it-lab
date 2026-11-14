# AAC-005: Planted problems with a sealed held-out answer key, scored by precision and recall

**Date:** 2026-11-14 · **Status:** Accepted; planned, not built. Follows lab ADR-001, ADR-013 and ADR-029.

**Context:** The lab's target for every project is precision and recall of at least 0.90 on planted problems, scored on a held-out seed (lab brief). If the problems are planted by code that the builder can read, a detector can be shaped by the exact cases it will be scored on. tier0 solved this for its messages: held-out wording written blind, sealed by hash and unsealed only by name (lab ADR-029).

**Decision:**

- **Planted problem types** (tuning-world counts are public; held-out counts are sealed and differ):

  | Problem | What the plan must do | Tuning count |
  |---|---|---|
  | Orphaned account (an account with no active person) | Flag and propose removal | 23 |
  | Mover with stale access | Remove the old role's apps, add the new | 11 |
  | Leaver with access left | Remove everything; suspend first where the API allows | set in milestone 2 |
  | Rehire | Match to the right person, never to the old account by name alone; grant the new role | set in milestone 2 |
  | Contractor past their end date | Remove | set in milestone 2 |
  | Leave of absence | Suspend, don't remove; restore on return | set in milestone 2 |
  | Bad HRIS data, e.g. an accidental mass termination | Catch it: the blast-radius check blocks the plan for two approvers | at least 1 event |
  | Drift (changed outside the process) | Flag and propose a revert | set in milestone 2 |
  | Break-glass account | Never touch; report only | set in milestone 2 |
  | Identity puzzles: username-only exports, name changes, duplicate names | Match correctly, or leave unmatched and ask; never guess a removal | set in milestone 2 |

- **The held-out world is written blind and sealed:**
  - A helper agent writes the held-out **recipe** (counts per type, which variants of mess, which edge cases, on which simulated days) without opening any Access-as-Code code, only the dataset's public schema. The agent that builds Access-as-Code never reads the recipe.
  - The recipe is sealed by SHA-256 with count-only tests, as held-out v3 is. The held-out world is generated from it only for the final score.
  - **Unsealed only by name:** `--final-score=held_out_a1` and nothing else.
  - Once scored, it's spent and becomes a dev set; a later final score needs a new sealed recipe.
- **Scoring:**
  - A finding is (problem type, person or account, app). Precision is correct findings over all findings; recall is planted problems found over all planted. Both are reported per type, with 95% intervals bootstrapped over people. Counts are small, so the intervals are wide and shown.
  - **Identity matching** is scored separately on account-to-person pairs, for rules only and for rules + AI, with the same intervals.
  - **Safety counts** are reported apart from precision and recall, and must be 0: changes to break-glass accounts, changes without a merged plan, removals over the limit without two approvals, removals resting on an unreviewed AI match.
  - Graded by code from the final mock state and the plan, never by a model.

**Consequences:**

- The final numbers measure detection on cases the code was never shaped by.
- Tuning counts are public, so a reader can check the tuning results against the generator.
- A blind recipe still comes from the lab's own idea of what goes wrong, an honest limit in the brief.
- Each new final score costs a new blind recipe and seal.
