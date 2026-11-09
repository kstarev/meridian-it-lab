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

## ADR-010: Memory as a reviewed Git repo

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** Long-term memory is the main target for memory poisoning (ASI06): whatever gets in shapes every later answer. A store the agent writes to directly has no review step and a weak history.

**Decision:** tier0's memory (learned runbooks, KB articles, resolved-ticket patterns) lives in a separate repo, meridian-memory, public for the portfolio with fake data only. The answering agent reads it read-only from a pinned commit; only the learning loop's GitHub App can open PRs. A ruleset on main requires a PR, code-owner review by the repo owner, signed commits and passing checks, with no bypass. Secret scanning and push protection are on. Entries carry provenance and a SHA-256 hash, CI scans every PR, retrieval code drops unverified, expired or tampered entries, and entries expire after 90 days unless re-verified. Evals use a local Git stand-in with the same checks. OWASP Agent Memory Guard is evaluated against the ASI06 attack set and adopted only if it catches attacks our checks miss without blocking clean entries; the outcome gets its own ADR.

**Consequences:**

- Every memory change has a reviewer, a diff and a revert path, so poisoning becomes a code-review problem that evals can test: 0 poisoned entries merged.
- GitHub never lets a PR author approve their own PR, so every change, including the owner's, is a PR opened by the bot.
- Because the repo is public, anyone can open a PR from a fork. A required check fails any PR the bot didn't open, so outside PRs are scanned and visible but never merged, and reviewers can see blocked poisoning attempts.
- The GitHub App's tokens expire within an hour (ADR-002), and its API commits are signed by GitHub.
- Git isn't a search index, so retrieval needs an index built from a pinned commit.
- A real company would keep memory private instead: private repos in an organisation with GitHub Secret Protection and Code Security.

## ADR-011: Security baseline

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** tier0 is an agent with tools and credentials, and its supply chain (actions, dependencies, the MCP server) is part of the attack surface (ASI04, LLM03). Controls are only credible if each one traces to a framework item and a check.

**Decision:** `docs/security.md` maps tier0's controls to the OWASP Top 10 for Agentic Applications 2026, the OWASP Top 10 for LLM Applications 2025, NIST SSDF (SP 800-218 and 800-218A) and the OWASP secure MCP server guide. CI runs CodeQL, Dependabot, gitleaks and an SBOM per release. Actions are pinned to commit SHAs and checked by zizmor. Workflow tokens are read-only by default, with per-job grants.

**Consequences:**

- Every control traces to a framework item and an automated check.
- With both repos public, CodeQL, secret scanning and push protection cost nothing.
- Pinned SHAs go stale, so Dependabot opens PRs to update them.
- Each PR takes longer in CI.

## ADR-012: Stack and pinned versions for milestone 1

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** Milestone 1 (ADR-009) is the first code in the lab, so it sets the stack the later projects build on. The scope is frozen, so new tools need a record, and ADR-007 asks us to pin the version of the GenAI conventions we follow. The milestone also has to square "one trace per ticket" with ADR-007's one trace per work session.

**Decision:**

- **Project:** one uv workspace on Python 3.13 with two packages, `dataset/` (`meridian_dataset`) and `tier0/` (`tier0`). tier0 depends on dataset, never the reverse. One `uv.lock` is committed, and `make` targets run with `--locked`.
- **Dataset:** the standard library's `random.Random(seed)` and our own name lists, not Faker, so the same seed always gives the same bytes.
- **Mocks:** in-process Python classes that return the real APIs' JSON shapes (Google Workspace Directory, Slack, Linear). They open no network ports, and each one logs every read for grading.
- **Identity and MCP:** the official MCP Python SDK over Streamable HTTP, run in-process with no network port. A mock issuer mints 5-minute ES256 JWTs with keys generated per run, and the server checks signature, expiry and audience. If the in-process HTTP transport doesn't work, the fallback is the SDK's in-memory transport with the token checked when the session opens.
- **Models:** one adapter interface with three implementations: a deterministic stub (the default), the Claude API (optional extra) and local Ollama over its REST API, using `httpx2`, which the MCP SDK already brings.
- **Tracing:** the OpenTelemetry SDK, following GenAI semantic conventions 1.44.0. Traces are written to `out/traces/` as OTLP/JSON lines, the OpenTelemetry Collector's file format. Following ADR-007, each work session is one trace: a ticket closed after 3 days with no reply has two traces that share its ticket ID.
- **Tests:** pytest and ruff. A test fixture blocks every outbound network connection.
- **Pinned versions** (checked on PyPI on 2026-11-08; `uv.lock` pins everything else): uv 0.13.7, `mcp` 2.3.0, `opentelemetry-api` and `opentelemetry-sdk` 1.45.2, `opentelemetry-semantic-conventions` 0.66b2 (conventions 1.44.0), `anthropic` 2.6.0, pytest 9.2.0, ruff 0.17.4.

**Consequences:**

- `make test`, `make eval` and `make demo` run offline, free and with no credentials.
- The same seed and lockfile give the same data and dependencies on every machine.
- Without Faker we maintain our own name lists, but a library upgrade can't silently change the data.
- In-process mocks don't exercise real HTTP. The ADR-004 contract suite and the demo workspaces cover that gap later.
- MCP SDK v2 and the GenAI conventions are young, so upgrades can break things. Dependabot (ADR-011) proposes version bumps, and following a newer conventions version needs a new ADR.

## ADR-013: Tuning and held-out messages never share wording

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** ADR-001 says we tune on one seed and score on a held-out seed. But the seed only changed which people and templates were drawn from one shared pool of message templates, so the held-out tickets reused the exact sentences we tuned on. A score on that seed said little about wording the system had never seen.

**Decision:** Message templates come in two separate pools, `tuning` and `held_out`, with different wording for every scenario. The held-out seed (2611) uses only the held-out pool; every other seed uses the tuning pool. A test proves the pools share no sentence, and that no ticket text from the tuning seeds appears in the held-out world. Nothing — code, prompts, stub rules or tests — is changed because of a held-out result: if something fails there, we explain it, propose a fix and check it on tuning data first.

**Consequences:**

- Held-out scores now measure wording the system was never tuned on.
- The stub model's keyword rules were written for tuning wording, so its pipeline check may score lower on the held-out seed. That is expected and is not "fixed".
- Every new ticket type needs wording for both pools, which is more dataset work.
- The generated data for the held-out seed changes, so the generator version goes to 0.2.0.

## ADR-014: The first held-out set is now a dev set

**Date:** 2026-11-08 · **Status:** Accepted

**Context:** ADR-013 gave the held-out seed (2611) its own wording. The first real-model run on it ([docs/evals/2026-11-08-ollama-llama3.2.md](evals/2026-11-08-ollama-llama3.2.md)) and a stub run showed us its failures, and the next milestone's fixes (a prompt fix, a second "own account only?" check, the choice of model) were designed with those failures in view. A score on that set would now overstate how well tier0 handles wording it has never seen.

**Decision:** The current held-out wording (held-out v1, seed 2611) becomes a dev set: we may look at its results while building fixes, but they no longer count as held-out scores. Before any fix is scored, we write held-out v2 wording, and nobody looks at results on it until the final comparison. The 2026-11-08 report stays as published; it was a true held-out result when it was made. Until v2 exists, real-model runs still default to seed 2611, and reports label it as a dev set.

**Consequences:**

- Every held-out set gives one honest final score, then becomes a dev set once its failures have shaped a change.
- Writing fresh wording for each scoring round is ongoing dataset work.
- Results from different rounds come from different sets, so every report names the set it ran on.

## ADR-015: Held-out v2 is sealed until milestone 2's final comparison

**Date:** 2026-11-09 · **Status:** Accepted

**Context:** ADR-014 made held-out v1 a dev set. Milestone 2 changes the triage prompt and adds a second "own account only?" check, then compares models. That comparison is only honest on wording that nobody tuned against and that has never produced results anyone has seen.

**Decision:**

- **Held-out v2** is a new message pool, `held_out_v2`, used by seed 3107. It has 11 templates: 5 normal, 2 duplicate name, 2 colleague, 2 outsider. Tests check that it shares no template, no sentence and no generated ticket text with the tuning pool or held-out v1.
- **Written blind:** a separate author wrote it without reading tier0's prompts or stub rules, and the person writing milestone 2's fixes has not read it. Tests print counts, never wording.
- **Sealed by hash:** SHA-256 of the pool's canonical JSON form, `fb078063c5fcd31b000b326ffe6fe15c5724bdde1f9da72471d31d8941c6a488`. It is stored in `meridian_dataset/seal.py`, and a test fails if the wording changes. For information, at generator version 0.3.0 seed 3107's `tickets.json` is `39ef7b204cb23ac9dbfff7c095305d2d14c60d59f9abe8ad96d3bdac26ada709` and its `answer_key.json` is `d6abf5ed0d6b540eb0b6196c116918ce46c702f7e71b74742bcaad2f510ab7db`.
- **No model runs on it,** the stub included, until the prompt fix and the second check are done. Every agent run refuses it unless `--final-comparison` is passed; `make eval` never passes it, and the demo always refuses it.
- After the final comparison, held-out v2 becomes a dev set, as v1 did (ADR-014).

**Consequences:**

- The final comparison measures wording nobody tuned against.
- The file hashes also depend on generator code, so they are a record, not a seal; a generator change that alters seed 3107's tickets needs a new ADR.
- Writing blind means the person building the fixes can't sanity-check v2's wording; the repo owner reviews it instead.
