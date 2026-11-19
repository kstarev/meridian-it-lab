# Shadow AI Radar decisions

Short records of the choices that shape Shadow AI Radar (`oauth-radar`), one file each, numbered OAR-001 onwards. The lab's own decisions are in [docs/decisions.md](../../../docs/decisions.md) and are cited as "lab ADR-NNN"; Access-as-Code's are in [access/docs/adr/](../../../access/docs/adr/README.md) as AAC-NNN. To change a decision, add a new record that supersedes the old one instead of editing it.

All seven are still Proposed. The owner's catalogue (2026-11-19) differs from several of them; the differences are listed in [catalogue-differences.md](../catalogue-differences.md).

| ID | Decision | Status |
|---|---|---|
| [OAR-001](001-project-layout.md) | `oauth-radar/` in the lab repo, with its own package, brief and decisions | Proposed; for the owner's review |
| [OAR-002](002-risk-model-rules-first.md) | The risk model: five signals, two overrides, scored by rules first | Proposed; for the owner's review |
| [OAR-003](003-grants-enter-the-shared-dataset.md) | OAuth grants join the shared dataset in their own module, and no existing world moves | Proposed; for the owner's review |
| [OAR-004](004-read-only-never-revoke.md) | Read-only: the radar opens a review and never revokes anything | Proposed; for the owner's review |
| [OAR-005](005-offline-export-shaped-files.md) | Offline only: export-shaped files, never an API, and never message content | Proposed; for the owner's review |
| [OAR-006](006-practice-and-sealed-held-out-worlds.md) | Tuned on a practice world, scored once on a held-out world written blind and sealed | Proposed; for the owner's review |
| [OAR-007](007-rules-decide-ai-explainer-later.md) | No AI in milestones 0 to 3; an optional AI explainer later needs a budget item and the owner's approval | Proposed; for the owner's review |

## Template

```markdown
# OAR-NNN: Title

**Date:** YYYY-MM-DD · **Status:** Proposed | Accepted | Superseded by OAR-NNN

**Context:** Why a decision is needed (2–3 lines).

**Decision:** What we chose.

**Consequences:** What gets easier, what gets harder.
```
