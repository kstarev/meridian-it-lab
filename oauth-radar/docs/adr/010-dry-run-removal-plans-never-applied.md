# OAR-010: Removal is planned, never applied: notice first, then a plan a person reviews

**Date:** 2026-11-19 · **Status:** Accepted (owner, 2026-11-19). Supersedes [OAR-004](004-read-only-never-revoke.md).

**Context:** OAR-004 said the radar only reads and reports, and that revoking would need a superseding decision with a plan, a dry run, a blast-radius limit and an allowlist. The owner's catalogue asks for a policy engine that removes "dormant restricted-scope grants after a 7-day notice", routes "everything else to a human", has a "known-compromised app" emergency rule with a human trigger, and keeps an exception list with an expiry date. Milestone 1 built all of it as dry runs ([catalogue-differences.md](../catalogue-differences.md), rows 8 to 12, 20 and 23). On 2026-11-19 the owner accepted every default, with two changes: what "last used" means, and a longest term for an exception.

**Decision:**

- **The radar plans; it never applies.** The mock Admin API has no call that changes anything, and the radar has none either. A plan is a file that says `"dry_run": true` and `"applied": false`, and names the action "revoke" only as data, with how a person would do it by hand in the admin console. Applying a plan from the radar would need a new decision that supersedes this one.
- **For each scored grant, on a simulated date, in this order:**
  1. **Compromised app** (on Security's list): into the **emergency plan**, even under an exception or in daily use. It skips the notice and must be triggered by a person; the radar never triggers it.
  2. **A current exception** covers the grant: no action, listed as excepted.
  3. **Dormant with a restricted scope** (unused for more than 90 days, and at least one scope Google classes as restricted; an unknown scope isn't restricted): a **notice draft** first; once the notice is 7 days old and the grant still unused, into the **after-notice plan**. A grant used after its notice is no longer dormant, and its notice lapses.
  4. **Restricted, with no record of use:** to a person.
  5. **High or medium** by the rubric ([OAR-008](008-risk-rubric-with-the-owners-changes.md)): to a person.
  6. **Low:** no action; only counted.
- **"Last used" counts only use after the grant (change 1).** The authorize event that made the grant isn't use. A grant nobody has used since it was granted has no record of use.
- **Exceptions:** each names an app, one user or everyone, a reason, who approved it, when, and an expiry date. It counts through its expiry day; after that it is ignored and reported. **An exception lasts at most 90 days (change 2).** One with a longer term, or with no expiry, is refused when the list is loaded and reported. It is never trimmed to 90 days, and a refused exception protects nothing.
- **Blast-radius limit:** a plan may touch at most 10 grants. A plan over the limit is refused whole, nothing partial, and every grant in it goes to a person. The same limit applies to the emergency plan.
- **Drafts, never messages:** notice drafts and Slack nudge drafts are files. A nudge goes to a grant the rubric rates high, used within 90 days, to an app IT hasn't approved, and names IT's approved alternative in the same category if there is one. Nothing is sent.
- **The weekly report** is built only from the run's files and groups what a person should review by app; people are named only in plan items and drafts, because IT needs them to act.
- **Idempotent:** the same inputs, notice ledger and date give byte-identical files, and a notice is never issued twice.
- **The source guard stays as strict as it is:** a test fails if any name in the package contains "revoke" or "delete".

**Consequences:**

- The worst a false positive can do is a notice draft, or a plan line a person declines.
- Fixing a risky grant still takes a person's time. "Removed" in any number means "in a reviewed plan".
- An exception has to be renewed every 90 days at most, so a business need is looked at again at least four times a year. A list with a bad entry still loads; the bad entry is listed, not hidden.
- A restricted grant nobody has used since it was granted goes to a person rather than into a plan: with no use on record, the radar doesn't plan its removal on its own.
