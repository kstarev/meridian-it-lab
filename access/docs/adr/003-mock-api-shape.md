# AAC-003: Mocks shaped like Okta, the Google Admin SDK and Slack SCIM, plus messy CSV exports

**Date:** 2026-11-14 · **Status:** Accepted; planned, not built.

**Context:** Access-as-Code reads and changes accounts in Meridian's identity provider, its directory, Slack and 95 apps without SSO. Real tenants are out of scope (lab brief). The mocks must be close enough to the real APIs that the collectors and apply code would carry over, and they must make the hard parts real: paging, rate limits, partial failures and retries.

**Decision:**

- **Three stateful, resettable mocks, in process** (no server), each shaped like a public API:

  | Mock | Shaped like | Used for |
  |---|---|---|
  | Okta | Okta Management API: users, apps, app user assignments, user lifecycle (deactivate, suspend, unsuspend), groups | 45 SSO apps: read assignments, unassign, deactivate |
  | Google | Google Admin SDK Directory API: users (suspend, org unit), groups and members | The directory: suspend and unsuspend, group membership |
  | Slack | Slack SCIM API: users (`active` true or false), groups | Deactivate and reactivate, user groups |

- **Same behaviour as the real ones, where it matters:** cursor paging, `429` with `Retry-After`, a share of transient `5xx` errors (seeded), and the real field names. Each mock has a contract test against recorded example responses, as tier0's ticket adapter has (lab ADR-004).
- **95 apps without SSO** are CSV exports, one per app, generated messy on purpose: different column names for the same thing (`Email`, `user_email`, `Login`, `Username`), email addresses in some and usernames in others, display names, four date formats (ISO 8601, `DD/MM/YYYY`, `MM/DD/YYYY`, `D Mon YYYY`), extra columns, blank rows and a few encoding problems. They can only be read; removing access from them is a **manual task** in the plan, checked against the next export.
- **Credentials:** the mocks accept only a scoped token for each collector or applier, so a test can show that a read-only collector can't write. Nothing in the model's path has a token ([AAC-007](007-safety-rails.md)).
- **State between nightly runs** lives on a bot-only branch, `access-state`: the actual state after the last apply, the log of applied changes and the spend ledger. Only the plan and apply workflows write to it. Tests and evals never need it; they build state from the seed.
- **The nightly demo steps through a simulated month** on the tuning world, one simulated day per night: HRIS events (joiners, movers, leavers, leave, a rehire, the planted HRIS error) and drift happen on set days. Then it starts again.

**Consequences:**

- Collectors and apply code are written against API shapes, so moving to a real tenant would mean swapping the client, not redesigning.
- Rate limits and errors make idempotency and retries something tests check, not just a claim.
- The mocks are ours, so they can't show every quirk of a real tenant (an honest limit in the brief).
- The state branch is shared mutable state; the workflows use one concurrency group so two runs never write to it at once.
