# Shadow AI Radar decisions

Short records of the choices that shape Shadow AI Radar (`oauth-radar`), one file each, numbered OAR-001 onwards. The lab's own decisions are in [docs/decisions.md](../../../docs/decisions.md) and are cited as "lab ADR-NNN"; Access-as-Code's are in [access/docs/adr/](../../../access/docs/adr/README.md) as AAC-NNN. To change a decision, add a new record that supersedes the old one instead of editing it.

On 2026-11-19 the owner accepted the catalogue's design and milestone 1's defaults, with a few changes. OAR-008 to OAR-011 record that, and supersede OAR-002 to OAR-005. OAR-001, OAR-006 and OAR-007 are still Proposed. Where the catalogue and the scaffold differ is listed in [catalogue-differences.md](../catalogue-differences.md).

| ID | Decision | Status |
|---|---|---|
| [OAR-001](001-project-layout.md) | `oauth-radar/` in the lab repo, with its own package, brief and decisions | Proposed; for the owner's review |
| [OAR-002](002-risk-model-rules-first.md) | The risk model: five signals, two overrides, scored by rules first | Superseded by OAR-008 |
| [OAR-003](003-grants-enter-the-shared-dataset.md) | OAuth grants join the shared dataset in their own module, and no existing world moves | Superseded by OAR-009 |
| [OAR-004](004-read-only-never-revoke.md) | Read-only: the radar opens a review and never revokes anything | Superseded by OAR-010 |
| [OAR-005](005-offline-export-shaped-files.md) | Offline only: export-shaped files, never an API, and never message content | Superseded by OAR-011 |
| [OAR-006](006-practice-and-sealed-held-out-worlds.md) | Tuned on a practice world, scored once on a held-out world written blind and sealed | Proposed; for the owner's review |
| [OAR-007](007-rules-decide-ai-explainer-later.md) | No AI in milestones 0 to 3; an optional AI explainer later needs a budget item and the owner's approval | Proposed; for the owner's review |
| [OAR-008](008-risk-rubric-with-the-owners-changes.md) | The risk rubric: OAR-002's points, an approved app never high on reach alone, the vendor directory's AI flag first; "found" means acted on | Accepted (owner, 2026-11-19) |
| [OAR-009](009-tuning-world-month-people-and-access-leavers.md) | The tuning world: grants for the month roster's people, leavers from the Access world's HRIS, a banned-app grant and a leaver among the plants | Accepted (owner, 2026-11-19) |
| [OAR-010](010-dry-run-removal-plans-never-applied.md) | Removal is planned, never applied: notice first, then a plan a person reviews; exceptions last at most 90 days | Accepted (owner, 2026-11-19) |
| [OAR-011](011-mock-admin-api-collector.md) | Grants come from a mock Google Admin API, in process; "last used" counts only use after the grant | Accepted (owner, 2026-11-19) |

## Template

```markdown
# OAR-NNN: Title

**Date:** YYYY-MM-DD · **Status:** Proposed | Accepted | Superseded by OAR-NNN

**Context:** Why a decision is needed (2–3 lines).

**Decision:** What we chose.

**Consequences:** What gets easier, what gets harder.
```
