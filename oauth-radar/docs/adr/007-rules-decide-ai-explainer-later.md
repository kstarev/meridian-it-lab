# OAR-007: No AI in milestones 0 to 3; an optional AI explainer later needs a budget item and the owner's approval

**Date:** 2026-11-15 · **Status:** Proposed; for the owner's review. Follows lab ADR-037, which gives this project nothing from the Claude credit.

**Context:** Lab ADR-037 splits the $100 Claude credit between tier0, Access-as-Code and a buffer, and says "Shadow AI Radar (oauth-radar) gets nothing from this credit". The credit ends on 2026-12-17. The lab's pattern is that the model interprets and code enforces (lab ADR-028, [AAC-006](../../../access/docs/adr/006-model-choice-and-spend-cap.md)). The radar's job, finding and ranking grants, is done by rules ([OAR-002](002-risk-model-rules-first.md)), so it needs no model to work.

**Decision:**

- **Milestones 0 to 3 make no AI call,** paid or local. The package imports no AI SDK and depends on nothing ([`test_guards.py`](../../tests/test_guards.py)). Nothing in `budget/` changes.
- **Milestone 4, an AI explainer, is optional.** If the owner wants it, it writes one short plain-English paragraph per app for the review ticket draft: what the app is, what its scopes reach, why it was flagged. It:
  - never decides: it can't add, drop or re-rank a finding, or change a score or tier;
  - sees only the finding's fields (app name, scopes, counts, rule reasons), never names of people;
  - is checked by code: every app name, scope and number in its text must be in the finding, or the ticket uses the template text instead, as Access-as-Code's summary does.
- **Before any call, it needs:**
  - a budget item for `oauth-radar` in a new lab budget decision, with a cap a spend ledger enforces;
  - the owner's approval of that item in `budget/`, by a pull request, as ADR-037 requires for every item;
  - a choice between a hosted model and a local one. A local model costs nothing but still needs the owner's go.

**Consequences:**

- The radar is complete without AI: rules find, rank and explain every finding in fixed words.
- No money is at risk, and nothing in this project can draw on tier0's or Access-as-Code's budget.
- The ticket's wording stays plain and template-like until the owner decides on milestone 4.
