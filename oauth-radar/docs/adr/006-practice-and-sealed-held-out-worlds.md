# OAR-006: Tuned on a practice world, scored once on a held-out world written blind and sealed

**Date:** 2026-11-15 · **Status:** Proposed; for the owner's review. Applies the lab's rules (ADR-001, ADR-003, ADR-013, ADR-029) and Access-as-Code's ([AAC-005](../../../access/docs/adr/005-sealed-answer-key.md), [AAC-010](../../../access/docs/adr/010-human-rulings-before-publishing.md)).

**Context:** "100% of high-risk grants found" means little if it's measured on the grants the rules were written for. The lab already knows how to stop that: tune on one world, score on another nobody tuned on (lab ADR-001, ADR-013), written blind and sealed (lab ADR-029), graded by code against an answer key (lab ADR-003), with a person ruling on every disagreement before anything is published (AAC-010).

**Decision:**

- **The practice world** (milestone 1) has a public recipe. Rules, the scoring table and the app catalogue are tuned on it freely, and its numbers are reported beside the held-out ones.
- **The held-out world** (milestone 3) has a recipe written blind:
  - A helper agent writes it without reading the radar's code, rules, scoring table, app catalogue or the practice recipe's planted grants. It may read the public guide: the problem types and file shapes in this folder and the brief.
  - It can bring new mess inside those shapes: new app names and publishers, new scope mixes, AI apps the catalogue doesn't know, leavers on new dates.
  - It's sealed by SHA-256, with count-only tests, and built only with `--final-score=<its name>`. The builder never reads it and never runs it before the final score.
- **Frozen first.** Before the held-out run, the rules, the scoring table and the app catalogue are frozen by hash. Nothing changes because of a held-out result (lab ADR-013).
- **Graded by code against the answer key.** The radar's findings file is compared with the answer key: no model judges. Its input files must be byte-identical after the scan (lab ADR-003's "final state", for a tool that changes nothing).
- **The metrics:**

  | Metric | Target |
  |---|---|
  | Recall on high-risk grants: planted high-risk grants the radar marks high | 100% (lab brief) |
  | Precision: grants the radar marks high that the key lists as high-risk | Reported; the lab brief's target for every project is ≥ 0.90 |
  | Recall and precision per problem type | Reported |
  | Grants revoked or changed, API calls, AI calls | 0 |
  | Same input, same findings | Always |

  Every rate is reported with its count and a 95% Wilson interval, because the counts are small.
- **Scored once.** Every disagreement between the radar and the key goes to the owner for a ruling, logged in `docs/evals/<date>-oauth-radar-held-out-rulings.md`, before any number is published. Key errors are reported as key errors, apart. Nothing is re-run after the rulings.
- **Then it's spent.** The held-out world becomes a dev set, as tier0's and Access-as-Code's did.

**Consequences:**

- The recall number means something: the held-out grants were written by someone who never saw the rules.
- The held-out world can only bring mess within the shapes and problem types written down here. A kind of risk nobody described can't appear in it.
- The key and the radar still share a builder, so one misreading could be in both. The owner decides in milestone 3 whether a blind referee is needed, as [AAC-011](../../../access/docs/adr/011-blind-referee-against-shared-mistakes.md) added for Access-as-Code.
- 100% on a small world is a weak claim: 20 of 20 still has a 95% interval that starts at about 84%. The report shows the interval beside the 100%.
