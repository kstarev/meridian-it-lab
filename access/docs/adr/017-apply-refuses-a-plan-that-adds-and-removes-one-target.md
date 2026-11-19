# AAC-017: Apply refuses a plan that adds and removes one target

**Date:** 2026-11-19 · **Status:** Accepted (the owner's ruling, 2026-11-19: "apply refuses a plan that adds and removes the same target"). Built in PR #122.

**Context:** The apply property tests (PR #111) never drew a plan that both adds and removes one target, because the diff never asks for both. Apply would have run such a plan. It takes access away before it adds, so the access would be gone and then back, on every run: a second apply would not be a no-op. Apply only runs a plan that matches a fresh rebuild, so today only a bug in the diff could bring such a plan this far.

**Decision:**

- **A target** is the system, the app id and the account, trimmed and in any case. That is the key apply uses to find an account in the state, and the one the diff keys its changes by.
- **Taking away** is a `remove` or a `suspend`, or a `flag` on an export row (it becomes a by-hand removal task). A `flag` on a directory account is a review, and one on an HR record is a correction, so neither counts.
- **The whole plan is refused, and nothing is applied,** in a dry run too. The reason names every such target, sorted: `contradictory plan: it adds and removes ana@meridian.example (okta/amber-board)`. The command line prints it and exits 1, as it does for every refusal.
- **It is checked on the plan file alone,** right after the version and part checks: before the approvals check and before the state is read. More approvals would not fix such a plan, so "needs 2 approvals" would mislead.
- **Every row of the plan's `changes` counts,** break-glass rows too: the check runs before the break-glass guard drops anything. The `held` list does not count, because this part never applies it.
- **Tests:**
  - Example tests in `test_apply.py`: the refusal on the tuning plan with one removal added, the command line's exit code, what counts as one target and as taking away, and a check that the diff's tuning plans have no such target.
  - `test_apply_properties.py`: generated plans may now add and remove one target. Apply refuses exactly those, before any read or write, whatever the mode, the approvals or the state, and with break-glass rows mixed in. The other properties still draw plans without such a target.
  - `tools/mutate.py`: `_takes_away` and `contradictions` are in the "access idempotent apply" target. Every new mutant (the two functions and the new refusal in `apply_plan`) was killed.

**Consequences:**

- A diff bug that adds and removes one target stops at apply, with the target named, instead of taking access away and giving it back.
- A test that wants the break-glass guard to drop both an add and a removal of one target needs two plans. `test_no_change_type_ever_touches_a_break_glass_account` now applies its adds in a plan of their own.
- Not covered:
  - A removal in one part and an add in the other. Each part is applied on its own, and applying one makes the other stale.
  - A Google alias and the primary email of the same user are two targets, because apply finds Google users by primary email. Apply makes no Google adds.
