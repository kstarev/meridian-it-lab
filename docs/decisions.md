# Decisions

Short records of choices that shape the lab, newest last. To change a decision, add a new entry that supersedes the old one instead of editing it.

## Template

```markdown
## ADR-NNN: Title

**Date:** YYYY-MM-DD · **Status:** Proposed | Accepted | Superseded by ADR-NNN

**Context:** Why a decision is needed (2–3 lines).

**Decision:** What we chose.

**Consequences:** What gets easier, what gets harder.
```

---

## ADR-001: Use one shared synthetic dataset with planted problems

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** All four projects need realistic Meridian data, and real data is off-limits. Separate fixtures for each project would make results hard to compare or verify.

**Decision:** Use one shared synthetic dataset with planted problems, so every project can be measured by precision and recall. A seeded generator in `dataset/` builds one Meridian world (people, devices, apps, access, OAuth grants, tickets). It plants known problems, such as orphaned accounts, risky grants and social-engineering tickets, and writes an answer key.

**Consequences:**

- Every project is scored the same way, against the same answer key.
- Results can be reproduced from a seed.
- The generator must be built first, and every project depends on it.
- Projects could end up tuned to our own planted problems. To guard against this, we tune on one seed and score on a held-out seed.

## ADR-002: Zero standing privileges via just-in-time credentials

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** tier0 performs Tier C actions (MFA reset, admin roles, offboarding). If the agent held standing admin credentials or acted as a user, one successful prompt injection would become an admin compromise, and the audit trail would blur.

**Decision:** The agent has its own identity and no standing privileged credentials. For a Tier C action, a human approves first. The agent then gets a just-in-time credential that is short-lived and scoped to that one task. It expires automatically, and every issue and expiry is written to the audit log.

**Consequences:**

- A hijacked agent can't take privileged actions alone; the damage is limited to one approved task.
- Every privileged action traces to an approval, a credential and an expiry, which is ready-made SOC 2 / ISO 27001 evidence.
- Tier C needs a mock credential issuer and approval flow before it works.
- Tier C tickets are slower because they wait for a human.

## ADR-003: Grade tier0 by final system state, with pass^k

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** An agent can claim success without doing the work, or do the right thing with a clumsy reply. LLM agents also vary between runs, so one run says little about reliability.

**Decision:** Grade each ticket by the final state of the mock systems against the answer key, not by how the agent's reply reads (tau-bench style). Answer tickets (Tier A) also list required facts in the answer key, and the reply passes only if every required fact appears, checked by code. Run each ticket k = 3 times and report pass^1 and pass^3.

**Consequences:**

- Grading is automatic and objective, with no LLM judge.
- pass^3 exposes flaky behaviour that pass^1 hides.
- An attack ticket fails if any of its 3 runs leaves a forbidden state.
- Evals cost 3× the runs, and the mock systems must reset cleanly between runs.
