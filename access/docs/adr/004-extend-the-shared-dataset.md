# AAC-004: The shared Meridian dataset is extended, never changed

**Date:** 2026-11-14 · **Status:** Accepted; planned, not built. Builds on lab ADR-001.

**Context:** Lab ADR-001 says every project uses one shared seeded dataset with planted problems. Today the generator (`meridian_dataset`, version 0.12.0) builds a thin slice for tier0: 10 people, Slack, tickets and an answer key. Access-as-Code needs the whole company: 230 people, HRIS records over a month, about 140 apps and their accounts. tier0's published results depend on its worlds staying byte-identical: its golden hash tests and its sealed held-out sets (lab ADR-015, ADR-029) would break if the existing generator changed.

**Decision:**

- **A new module in the shared package,** `meridian_dataset.access`, with its own generator version and its own seeds. It reuses what the lab already has: the fake name lists (`names.py`), the domain `meridian.example`, the username rule (first.last, with a number for duplicates) and the fact sheet (230 people, 14 countries, 3 hubs, the IT team of three).
- **`generate()` and tier0's worlds don't change.** A new test checks every tier0 world (tuning, v1, v2, v3) still has the same hash. A change that moves any of them fails CI.
- **The access world is its own 230-person company,** not tier0's 10 people plus 220 more. Joining them would change tier0's worlds. The two share the same rules and name lists, so they read as the same company.
- **What it generates:**
  - an HRIS export (fake, generic shape) with employee ID, name, personal and work email, job title as typed by HR, department, team, manager, employment type (employee or contractor), status, start, end and leave dates, and a timeline of changes over a simulated month;
  - Okta users and assignments to 45 SSO apps;
  - Google directory users and groups;
  - Slack SCIM users;
  - 95 CSV exports for the apps without SSO ([AAC-003](003-mock-api-shape.md));
  - the planted problems and an answer key, kept apart from everything the code under test reads ([AAC-005](005-sealed-answer-key.md)).
- **Seeds:** a tuning seed and a held-out seed of its own, chosen in milestone 2, never shared with tier0's seeds.

**Consequences:**

- Both projects use the same company and rules, so their results can sit side by side.
- tier0 is protected by a test, not by care.
- The fact sheet's "~140 apps, 45 behind SSO, 95 not" becomes real data for the first time.
- The shared package grows; it gets its own section in the dataset docs.
