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

## ADR-004: Ticket adapter with mock and real Linear

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** Meridian's ticketing tool is now Linear (previously Jira Service Management). tier0 opens, links and closes a Linear ticket for every request. Grading needs a ticket system that resets between runs and has a controllable clock for the 3-day close. The demo needs a real tool.

**Decision:** tier0 reaches tickets only through a ticket adapter with two implementations: a mock Linear for grading, and a free Linear workspace with fake data for the demo.

**Consequences:**

- Grading stays offline, fast and repeatable.
- The demo shows the same agent working in a real tool.
- The mock can drift from real Linear behaviour. To catch this, a small contract test suite runs against both adapters.
- Demo runs are never graded; only mock runs count.

## ADR-005: Weekly learning loop that adds knowledge, never permissions

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** The same issues recur, and human-resolved tickets show how they were fixed. But learning from ticket text opens a path to memory and context poisoning (ASI06). An agent that learns its way to new permissions would also undo zero standing privileges (ADR-002).

**Decision:** Once a week, tier0 reads human-resolved tickets, clusters repeat patterns and drafts runbooks as a pull request for review: by a scripted stand-in reviewer in simulated weeks, and by a human in the demo. Ticket text is untrusted input. Every learned ticket becomes a regression eval. A runbook goes live only after the PR is approved and the evals pass with 0 unsafe actions. Learning may add knowledge, never permissions.

**Consequences:**

- The share of tickets resolved without a human should rise week over week, and it's measured.
- The eval suite grows with every learned ticket, so regressions get caught.
- A poisoned ticket can at most propose a bad runbook, which still has to pass review and the eval gate.
- Simulated weekly gains rest on a scripted reviewer, so they show what the loop can do, not how a real reviewer would judge each runbook.
- The dataset must generate several weeks with recurring patterns, and eval cost grows over time.

## ADR-006: Self-only lookups with least-privilege directory and MDM access

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** Username, device and app questions are frequent Tier A tickets, and each one could leak someone else's data. Building usernames from first.last fails when names are duplicated. Real employer tenants are off-limits.

**Decision:** tier0 looks usernames up in the Google Workspace Directory API and never builds them from the pattern. It answers username, device and app questions only for the authenticated requester. That identity comes from the Slack event, and self-only tools take no user argument. Device requests go to Jamf or Intune by the requester's device. The agent's MDM credentials allow only device read, app inventory read and app assignment. Evals use mocks shaped like the Directory API, Jamf Pro API and Microsoft Graph.

**Consequences:**

- The dataset plants duplicate names, so a username built from the pattern is caught as wrong.
- Social-engineering tickets (ASI03) test that the agent refuses and routes to a verified recovery path.
- Real Graph has no permission that only allows assigning apps: assigning one requires DeviceManagementApps.ReadWrite.All, which can also edit apps. The narrow credential exists only in the mock, and a real deployment would need extra guardrails.
- Two MDM mocks to build and keep close to the real API shapes.

## ADR-007: Observability with OpenTelemetry GenAI conventions, private by default

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** To debug tier0 and know what each ticket costs, we need to see every LLM call, tool call and approval. Service desk traces are full of personal data, and GDPR and German works council rules limit what can be kept.

**Decision:** tier0 emits traces that follow the OpenTelemetry GenAI semantic conventions, with spans for LLM calls, tool calls and approvals. Each session of work on a ticket is one trace, and all of a ticket's traces carry its ticket ID. Cost comes from token usage and a price table, and latency from span timings, both summed per ticket. By default, traces hold no message content and only pseudonymised user IDs; content capture is on only in eval runs on fake data.

**Consequences:**

- Any OpenTelemetry backend works, with no vendor lock-in.
- Cost and latency per ticket are measured, not estimated.
- A ticket waiting 3 days for a reply spans several short traces instead of one long-lived trace.
- Traces without content are harder to debug; eval runs on fake data fill that gap.
- The GenAI conventions are still changing, so we pin the version we follow.

## ADR-008: Global kill switch and rate limit on agent actions

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** A hijacked or looping agent can do a lot of damage quickly (OWASP ASI08 Cascading Failures, ASI10 Rogue Agents). Approval tiers stop single bad actions, not a flood of small ones.

**Decision:** A global kill switch stops all agent actions at once and routes open tickets to humans. A rate limit caps agent actions, globally and per requester. The MCP server enforces both outside the model, so no prompt can turn them off. Every toggle, limit hit and override goes to the audit log.

**Consequences:**

- The worst case is bounded in both size and time.
- Both controls are tested in evals: 0 actions after the kill switch, 0 actions over the limit.
- Legitimate bulk work, such as onboarding a cohort of new hires, can hit the limit. A human can raise it, and the override is logged.
- While the switch is on, every ticket waits for a human.

## ADR-009: Walking skeleton as milestone 1

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** The plan now covers many features across four projects. Building it layer by layer risks finding integration problems late.

**Decision:** Milestone 1 is one ticket type end to end: Slack → triage → Linear ticket → answer → close → trace → eval. The ticket type is "What's my username?" (Tier A). It builds only the thin slice of the dataset it needs. Evals use a mock Slack; the demo uses a free Slack workspace with fake users.

**Consequences:**

- Every layer (identity, MCP, ticket adapter, tracing, grading) is proven early, on a simple ticket.
- An ASI03 attack eval (asking for a colleague's username) is in place from day one.
- It produces the first real numbers for the README and site.
- ADR-001 still holds: work starts with the dataset, but only the slice the skeleton needs, and the generator grows ticket type by ticket type.
