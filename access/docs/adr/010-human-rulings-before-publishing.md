# AAC-010: Every disagreement on the held-out score gets the owner's ruling, logged, before anything is published

**Date:** 2026-11-14 · **Status:** Accepted; applies at the final score in milestone 7. The user's decision (2026-11-14). The rulings it needs, and the open questions around them, are listed as M7-R10 to M7-R15 in [m7-prep](../m7-prep.md) (2026-11-15).

**Context:** The held-out answer key was written blind ([AAC-009](009-held-out-recipe-can-bring-new-mess.md)) and checked by code and a second blind reviewer, but a key can still be wrong. If a key error counted against the tool, the published score would be wrong, and nobody could tell a tool mistake from a key mistake.

**Decision:**

- The held-out world is scored **once**, at the end.
- **Before any number is published,** every disagreement between Access-as-Code and the key goes to the repo owner for a human ruling. A disagreement is any of these:
  - a finding the tool missed;
  - a finding the key doesn't have;
  - a different type or action for the same account.
- **Each ruling is logged** in `docs/evals/<date>-access-held-out-a1-rulings.md`: the item, what the tool did, what the key says, the ruling (the tool was wrong, the key was wrong, or both are acceptable) and a one-line reason.
- **A key error is reported as a key error.** It isn't counted against the tool, and the published report gives the number of key errors and lists them, beside precision and recall.
- **Rulings never lead to re-running the tool** or to changing it before the numbers are published. The score is the one run.

**Consequences:**

- Published numbers measure the tool, not the key's mistakes, and the rulings log shows exactly where the two differ.
- The owner reads the disagreements, and so sees held-out content, only at the end, after the tool is frozen. The world becomes a dev set right after (AAC-005).
- Scoring takes a human step before publishing. With a few hundred findings this is expected to take an hour or two.
