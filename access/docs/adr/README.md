# Access-as-Code decisions

Short records of the choices that shape Access-as-Code, one file each, numbered AAC-001 onwards. The lab's own decisions (tier0, the shared dataset) are in [docs/decisions.md](../../../docs/decisions.md) and are cited as "lab ADR-NNN". To change a decision, add a new record that supersedes the old one instead of editing it.

| ID | Decision | Status |
|---|---|---|
| [AAC-001](001-repo-layout.md) | `access/` in the lab repo, with its own package and the shared dataset | Accepted; planned |
| [AAC-002](002-the-plan-is-a-pull-request.md) | The plan is one rolling pull request; approval is a review; apply is the merge | Accepted; planned |
| [AAC-003](003-mock-api-shape.md) | Mocks shaped like Okta, the Google Admin SDK and Slack SCIM, plus messy CSV exports | Accepted; planned |
| [AAC-004](004-extend-the-shared-dataset.md) | The shared Meridian dataset is extended, never changed | Accepted; planned |
| [AAC-005](005-sealed-answer-key.md) | Planted problems with a sealed held-out answer key, scored by precision and recall | Accepted; planned |
| [AAC-006](006-model-choice-and-spend-cap.md) | The model interprets, code enforces; Haiku, Sonnet and Opus 5.5 compared on tuning data; a $3 cap (replaced by lab ADR-037) | Accepted; Haiku 5.5 picked (2026-11-15) |
| [AAC-007](007-safety-rails.md) | Dry-run by default, a blast-radius limit, a break-glass allowlist, no stale plans | Accepted; planned |
| [AAC-008](008-runs-on-github-only.md) | Runs on GitHub only; the summary lives in the plan pull request; Slack is a mock adapter | Accepted; planned |
| [AAC-009](009-held-out-recipe-can-bring-new-mess.md) | The held-out recipe can bring new kinds of mess, written blind and sealed out of the builder's reach | Accepted |
| [AAC-010](010-human-rulings-before-publishing.md) | Every disagreement on the held-out score gets the owner's ruling, logged, before anything is published | Accepted; applies in milestone 7 |
| [AAC-011](011-blind-referee-against-shared-mistakes.md) | A blind referee builds its own key from the raw held-out files, as the check against shared mistakes | Accepted |
| [AAC-012](012-title-map-seeded-from-the-practice-world.md) | The reviewed job-title map is seeded from the practice world; new titles stay unmapped | Accepted |

## Template

```markdown
# AAC-NNN: Title

**Date:** YYYY-MM-DD · **Status:** Proposed | Accepted | Superseded by AAC-NNN

**Context:** Why a decision is needed (2–3 lines).

**Decision:** What we chose.

**Consequences:** What gets easier, what gets harder.
```
