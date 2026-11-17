# Security mapping

This page maps tier0's controls to security frameworks. It is a mapping, not a certification. Meridian and all its data are fictional.

## OWASP Top 10 for Agentic Applications 2026

| ID | Risk | tier0 controls | Source |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | Ticket text and attachments are untrusted input; Tier C always needs a human; attack tickets in evals | Brief, ADR-003 |
| ASI02 | Tool Misuse and Exploitation | Tools grouped into data-classification zones; self-only tools take no user argument; least-privilege MDM credentials; rate limit | ADR-006, ADR-008 |
| ASI03 | Identity and Privilege Abuse | The agent's own identity; just-in-time, task-scoped Tier C credentials; MCP audience check; requester identity from the Slack event | ADR-002, ADR-006 |
| ASI04 | Agentic Supply Chain Vulnerabilities | Actions pinned to commit SHAs; Dependabot; SBOM; CodeQL; Agent Memory Guard pinned if adopted | ADR-011 |
| ASI05 | Unexpected Code Execution | No code-execution tool; runbooks can only call existing MCP tools | ADR-005 |
| ASI06 | Memory and Context Poisoning | Memory as a reviewed Git repo; provenance and hashes; CI scan of every memory PR; retrieval filter; 90-day expiry | ADR-005, ADR-010 |
| ASI07 | Insecure Inter-Agent Communication | tier0 is a single agent; every tool call goes through the MCP server, which checks token audience | Brief |
| ASI08 | Cascading Failures | Rate limit on agent actions, globally and per requester | ADR-008 |
| ASI09 | Human-Agent Trust Exploitation | The bot says it's an AI; Tier C approval and credential IDs are linked to the ticket; attack tickets test misleading approval requests | Brief, compliance.md |
| ASI10 | Rogue Agents | Global kill switch; no standing privileges; audit log of every credential and toggle | ADR-002, ADR-008 |

## OWASP Top 10 for LLM Applications 2025

| ID | Risk | tier0 controls |
|---|---|---|
| LLM01 | Prompt Injection | Ticket text is untrusted; ASI01 attack tickets; memory PR scan |
| LLM02 | Sensitive Information Disclosure | Self-only lookups; no message content in traces; PII scan on memory PRs |
| LLM03 | Supply Chain | Same controls as ASI04 |
| LLM04 | Data and Model Poisoning | The only thing tier0 learns is memory, which is reviewed and scanned (ASI06) |
| LLM05 | Improper Output Handling | Model output never runs as code; the MCP server validates every tool argument |
| LLM06 | Excessive Agency | Approval tiers; tool zones; least-privilege credentials; learning adds knowledge, never permissions |
| LLM07 | System Prompt Leakage | No secrets in prompts; credentials come from the token issuer, never the prompt |
| LLM08 | Vector and Embedding Weaknesses | The retrieval index is built from a pinned, verified commit and filtered before the model |
| LLM09 | Misinformation | Answer tickets must contain every required fact, checked by code; runbooks are reviewed |
| LLM10 | Unbounded Consumption | Rate limit; cost and latency tracked per ticket |

## NIST SSDF (SP 800-218 and SP 800-218A)

SP 800-218A adds generative-AI practices to the SSDF, such as protecting models and data from tampering and testing for AI-specific attacks.

| Practice group | tier0 controls |
|---|---|
| PO: Prepare the Organization | The brief, ADRs and this mapping set security requirements; the OWASP lists serve as the threat model |
| PS: Protect the Software | Rulesets, signed commits and code-owner review; secret scanning and push protection; pinned actions; SBOM; memory hashes |
| PW: Produce Well-Secured Software | CodeQL; gitleaks; zizmor; evals with attack tickets for prompt injection and poisoning |
| RV: Respond to Vulnerabilities | Dependabot alerts and updates; kill switch; learned tickets become regression evals |

## OWASP secure MCP server guide

Areas from the OWASP GenAI Security Project's *A Practical Guide for Secure MCP Server Development* (v1.0).

| Area | tier0 controls |
|---|---|
| Secure architecture | Tools grouped into public, internal and restricted zones; self-only tools take no user argument; controls enforced outside the model |
| Authentication and authorization | OAuth 2.1 resource server that rejects tokens not issued for it (audience check); the agent's own identity; just-in-time Tier C credentials |
| Input validation | Every tool argument is validated against its schema; ticket text is untrusted |
| Session isolation | Credentials are scoped to one task; self-only tools are bound to the requester's identity |
| Hardened deployment | Kill switch and rate limit enforced in the server; audit log; pinned dependencies and SBOM |

## CI baseline

| Check | Repo | Purpose |
|---|---|---|
| CodeQL | meridian-it-lab | Finds code vulnerabilities |
| Dependabot | Both | Alerts on vulnerable dependencies; opens PRs to update dependencies and pinned action SHAs |
| gitleaks | Both | Blocks secrets in commits |
| Secret scanning and push protection | Both | GitHub blocks known secret formats at push |
| zizmor | Both | Blocks unpinned actions, broad workflow permissions and template injection |
| Workflow tokens | Both | `permissions: {}` by default, with per-job grants |
| SBOM | meridian-it-lab | Generated for every release |
| Memory PR scan | meridian-memory | Prompt injection, secrets, PII (Presidio), size anomalies, provenance, tools or scopes above tier, PRs not opened by the bot |
| Evals | meridian-it-lab | Smoke eval on every PR; full k = 3 suite nightly and on main |

The workflow controls that are built today, the test that checks each one, and the settings that live in GitHub: [github-actions-hardening.md](github-actions-hardening.md).
