# OAR-008: The risk rubric: OAR-002's points, with the owner's two changes

**Date:** 2026-11-19 · **Status:** Accepted (owner, 2026-11-19). Supersedes [OAR-002](002-risk-model-rules-first.md).

**Context:** OAR-002 was written on 2026-11-15, before the owner's catalogue existed. The catalogue asks for "a transparent rubric (scope sensitivity × users × dormancy × vendor maturity)" and says "AI may write the explanation, never the score". Milestone 1 built OAR-002's points as written and listed every difference in [catalogue-differences.md](../catalogue-differences.md) (rows 2 to 7 and 19). On 2026-11-19 the owner accepted every default, with two changes to the rubric (ruling R14) and a ruling on what "found" means (R16).

**Decision:**

- **The unit is one grant:** one person, one app, and every scope that person gave that app. One app granted by 30 people gives 30 grants.
- **Points, not the catalogue's product.** The product has no weights or thresholds; the owner accepted OAR-002's points. Five signals:

  | Signal | How it's measured | Points |
  |---|---|---|
  | `scope_breadth` | The broadest scope the grant holds, from the scope explainer's classes: Google's restricted scopes and Admin SDK scopes are broad, sensitive scopes medium, non-sensitive scopes narrow. **A scope the table doesn't know counts as broad.** | broad 3, medium 2, narrow 0 |
  | `unverified_publisher` | Google doesn't list the publisher as verified. **A publisher the vendor directory doesn't know counts as not verified.** | +2 |
  | `reach` | People at Meridian holding a grant to the same app: 1 to 4 add nothing, 5 to 24 add 1, 25 or more ("org-wide") add 2. The mock has no admin installs, so they don't count yet. | 0, +1, +2 |
  | `ai_app` | **Change 2:** the vendor directory's AI flag. Only when the directory doesn't know the app, the short word list (`ai`, `gpt`, `llm`, `copilot`, `assistant`, `agent`, `transcribe`, `transcription`, as whole words in the app's or the publisher's name). | +1 |
  | `not_reviewed` | The app isn't in IT's app catalogue at all | +1 |

- **Two overrides make a grant high whatever its points:** an app on IT's banned list; and a leaver's grant (HRIS says terminated, or the end date has passed) that holds a scope above narrow. Leavers come from the Access world's HRIS records ([OAR-009](009-tuning-world-month-people-and-access-leavers.md)).
- **Tiers:** 5 points or more is high, 3 to 4 medium, 2 or fewer low. **Change 1: an app IT approved is never high because of its reach alone.** If an approved app's grant reaches 5 points only through `reach`, it is medium. It can still be high through its other signals or an override.
- **Worked examples:**
  - An unverified app with full Drive access: 3 + 2 = 5, high.
  - A verified AI note-taker that IT approved, granted by 25 people, reading calendars: 2 + 2 + 1 = 5, but 3 without reach, so medium. (OAR-002 rated it high.) If IT reviewed it and didn't approve it, it stays high.
  - A verified, reviewed app with full Drive for one person: 3, medium.
  - "Sign in with Google" for one person: 0, low.
- **The broadest scope counts, not the sum.** Ten small scopes don't outrank one that reads all mail.
- **Rules decide, and AI never sets a score.** Every score lists each signal with its points and a plain reason, so a person can recompute it. AI may later word an explanation ([OAR-007](007-rules-decide-ai-explainer-later.md)); it never changes a score.
- **The catalogue's other two factors:** dormancy is not a score signal; the policy engine uses it ([OAR-010](010-dry-run-removal-plans-never-applied.md)). Vendor maturity is shown in the report, not scored.
- **What "found" means (R16):**
  - **The headline is "acted on by the radar":** a planted risky grant is found when the radar writes a notice draft for it, puts it in a plan (after-notice or emergency), or drafts a Slack nudge for it. Being on the list for a person alone doesn't count. Precision is the share of grants acted on that the answer key lists.
  - **Targets:** precision at least 0.90, the lab's target for every project ([lab brief](../../../docs/brief.md)), reported as FAIL until it is met. Recall 100%: every planted risky grant, the lab brief's target for oauth-radar and the catalogue's "6 of 6".
  - **"Marked high by the rubric"** (OAR-002's definition) stays as a second line, reported without a target.
- **Where the numbers live:** in `rubric.py`, changed only by a pull request that says why. OAR-002's plan to move them into a reviewed `policy/scoring.json`, frozen by hash before a held-out run, isn't decided here; it waits with [OAR-006](006-practice-and-sealed-held-out-worlds.md).
- **The milestone-0 stub** (`rules.py`: `broad_scope` and `banned_app`, listed in OAR-002) stays for the milestone-0 file loader.

**Consequences:**

- The company's approved tools no longer fill the high list just for being popular. An approved app with a broad scope and an unverified publisher is still high.
- An AI app with a plain name is caught when the vendor directory knows it. The word list only covers apps nobody knows, and IT's own AI tag no longer counts.
- With "acted on" as the headline, a planted grant that only reaches the list for a person is a miss, and every grant the radar acts on that isn't planted is a false alarm against a 0.90 target.
- The points are still a judgement, not data. They are tuned on the tuning world only, and the report says so.
