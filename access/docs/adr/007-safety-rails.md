# AAC-007: Dry-run by default, a blast-radius limit, a break-glass allowlist, no stale plans

**Date:** 2026-11-14 · **Status:** Accepted; planned, not built.

**Context:** A tool that removes access across a whole company can do a lot of damage quickly: a bad HRIS export can make everyone look like a leaver, and removing the last admin account locks IT out. Review alone isn't enough if a reviewer approves a huge plan without reading it. Each rail below is enforced by code or GitHub settings, not by the model or by care.

**Decision:**

- **Dry run by default.** Apply prints and records what it would do unless the repository variable `ACCESS_APPLY_MODE` is `mock-live`, and then it changes only the mocks. There is no mode for real systems.
- **Blast-radius limit.** A plan that removes or suspends more than **N** accesses needs **two** code-owner approvals; below N, one. N is `25` to start (about two leavers' worth), set in `access/policy/settings.yaml`, so changing it is itself a reviewed change. A required check counts approvals from code owners on the current plan and fails until there are enough. Apply checks the count again before it changes anything.
- **Break-glass allowlist.** Accounts in `access/policy/break_glass.yaml` are never changed, by any plan. If a plan would touch one, that change is dropped and reported as a warning. A test proves no code path changes them.
- **No stale plans.** `plan.json` records the hash of the actual state it was built from. Before applying, apply collects the state again; if the hash differs, it stops and the next plan is built from fresh data.
- **Idempotent apply.** Each change is checked against the current state before it's made, so applying the same plan twice changes nothing the second time. Retries after a `429` or `5xx` are safe.
- **Suspend before remove,** where the API allows it, for leavers and people on leave; removal follows in a later plan.
- **AI suggestions never act on their own** ([AAC-006](006-model-choice-and-spend-cap.md)): a change that rests on an AI match or title mapping can't be in a plan until a person has approved that map entry.
- **Read and write are separate tokens.** Collectors hold read-only tokens; only apply holds write tokens, and only in the apply workflow.

**Consequences:**

- The planted mass termination is caught by design, and the results page shows it.
- Two approvers need two people. The lab has one, so the rule is shown with a second GitHub account or a scripted stand-in, and said plainly (an honest limit in the brief).
- N will be revisited with data in milestone 6; it's a policy setting, not code.
- Dry run by default means the demo shows plans and what apply would do; switching to `mock-live` is one variable.
