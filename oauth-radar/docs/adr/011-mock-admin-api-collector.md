# OAR-011: Grants come from a mock Google Admin API, in process; never a real API, never content

**Date:** 2026-11-19 · **Status:** Accepted (owner, 2026-11-19). Supersedes [OAR-005](005-offline-export-shaped-files.md).

**Context:** OAR-005 had the radar read export-shaped files only (a merged `tokens.list` file, the Admin console's app list, Slack's integration logs), never an API. The owner's catalogue has a collector that "pulls grants from a mock Google Admin token API: app, scopes, user, last used", with grants "shaped like Google's" ([catalogue-differences.md](../catalogue-differences.md), rows 13 to 15). Milestone 1 built that collector. On 2026-11-19 the owner accepted every default, and ruled that "last used" counts only use after the grant.

**Decision:**

- **The collector reads three read-only calls** of an in-process fake, each shaped like the real response for the fields the radar reads, not every field the real API returns:
  - Directory API `users.list`, paged;
  - Directory API `tokens.list` for one user, not paged, as in the real API;
  - Reports API `activities.list` for the `token` application, paged, newest first: the activity source for "last used".
- **No network and no credentials.** The package has no HTTP client and imports no network or AI library; a test opens no socket during a collection. The fake holds no token values and has no call that changes anything ([OAR-010](010-dry-run-removal-plans-never-applied.md)).
- **"Last used" is the newest use after the grant.** The authorize event that made the grant isn't use (the owner's ruling). With no use on record, `last_used` is empty.
- **Google only.** No Slack export is generated or read; Slack is where people get a nudge draft.
- **Everything else is a file** in the world's `radar/` folder: the vendor directory with Google's verified flag, IT's app catalogue, IT's banned list, Security's list of compromised apps, IT's exceptions, and the HRIS leavers ([OAR-009](009-tuning-world-month-people-and-access-leavers.md)). The answer key is never among them.
- **Never message or file content.** Every input is metadata: who, which app, which scopes, when. The collector reads only the fields it names; anything else in a page never reaches a record, and a grant has no field for content.
- **Nothing is dropped silently.** A page or file of the wrong shape stops the run and says where, because a skipped grant would be a missed finding. The one exception is an IT exception with too long a term or no expiry: it is refused and reported, and the run goes on (OAR-010).
- **The milestone-0 file loader** (a `tokens.list`-shaped export and a banned list) stays as it is.

**Consequences:**

- The radar still runs anywhere, with no setup and nothing to leak.
- Real APIs have rate limits, errors, retention limits and quirks the mock doesn't. A real audit log keeps only so much history, so "no record of use" would mean less there than it does here.
- A real deployment would need read-only admin credentials and a decision of its own.
