# OAR-002: The risk model: five signals, two overrides, scored by rules first

**Date:** 2026-11-15 · **Status:** Proposed; for the owner's review. The milestone-0 stub builds two rules; the rest is the plan for milestone 2.

**Context:** The lab brief's target for `oauth-radar` is "High-risk OAuth grants found: 100%". That needs a written answer to "what is high-risk?" before any data is generated, because the planted problems in the dataset ([OAR-003](003-grants-enter-the-shared-dataset.md)) follow it. The radar also has to put the worst grants first, so a three-person IT team reads them first. And this project has no Claude budget (lab ADR-037).

**Decision:**

- **The unit is one grant:** one person, one app, and every scope that person gave that app. A finding is one grant. One app granted by 30 people gives 30 grants. The ticket draft groups them by app.
- **Five signals, scored by points:**

  | Signal | How it's measured | Points |
  |---|---|---|
  | Scope breadth, and the data it reaches | The broadest scope the grant holds, from a reviewed scope table. Each scope names the data it reaches (mail, files, chat messages, calendar, contacts, directory, sign-in only) and a tier. **Broad:** all mail, all Drive files, all Slack messages or files, admin scopes. **Medium:** calendar, contacts, all spreadsheets, posting messages, reading the directory. **Narrow:** files the app made or was handed, sign-in and basic profile | Broad 3, medium 2, narrow 0 |
  | Publisher verification | Google: the app isn't verified. Slack: the app isn't in the Slack App Directory and isn't Meridian's own | +2 |
  | Reach: how many people | People at Meridian with a grant to the same app: 1–4 adds nothing, 5–24 adds 1, 25 or more, or installed for everyone by an admin ("org-wide"), adds 2 | 0, +1, +2 |
  | AI app | The app is tagged AI (meeting note-taker, writing assistant, AI agent) in IT's app catalogue; or, if it isn't in the catalogue yet, its name or publisher matches a short AI word list | +1 |
  | Not reviewed | The app isn't in IT's app catalogue at all, so it never went through ticket type 9 ("New app or integration approval") | +1 |

- **Two overrides make a grant high-risk whatever its points:**
  - **Banned app:** the app is on IT's banned list.
  - **Leaver:** the person has left (HRIS says terminated, or the end date has passed) and the grant still holds a scope above narrow.
- **Tiers:** 5 points or more is **high**, 3–4 is **medium**, 2 or fewer is **low**. Worked examples:
  - An unverified app with full Drive access: 3 + 2 = 5, high.
  - A verified AI note-taker in the catalogue, installed org-wide, reading calendars: 2 + 2 + 1 = 5, high. So AI apps with org-wide reach are high even with a medium scope.
  - A verified, reviewed app with full Drive for one person: 3, medium. A person checks it, but it isn't on the high list.
  - "Sign in with Google" for one person: 0, low.
- **The broadest scope counts, not the sum.** An app that asks for ten small scopes shouldn't outrank one that reads all mail.
- **A scope the table doesn't know counts as broad.** It fails toward a review, never toward silence, because a miss is the costly error.
- **The planted problem types follow the signals:** `broad_scope`, `unverified_publisher`, `ai_app_org_wide`, `leaver_grant` and `banned_app`. One grant can carry more than one. Each is high-risk by this table. The answer key also holds decoys: grants that look alike but aren't high-risk (a verified sign-in-only app, an AI app with `drive.file` only), so precision means something.
- **What "found" means for the metric:** a planted high-risk grant is found when the radar marks it high. Marking it medium or low is a miss. Precision is the share of grants the radar marks high that the answer key lists as high-risk.
- **Rules first, and rules decide.** The score comes from code and a table, never from a model, because:
  - every finding can say exactly which rule fired and why, in plain words, so IT can check it in a minute;
  - the same input always gives the same list, so a test can pin it;
  - it costs nothing, and this project has no budget (lab ADR-037);
  - recall is the target, and a rule can be made to lean toward flagging, with precision reported to keep that honest.

  An AI explainer may come later, to word the ticket. It never changes a score ([OAR-007](007-rules-decide-ai-explainer-later.md)).
- **The numbers live in a reviewed file from milestone 2,** `oauth-radar/policy/scoring.json`, changed only by a pull request. They're tuned on the practice world only, and frozen by hash before the held-out world runs ([OAR-006](006-practice-and-sealed-held-out-worlds.md)). The points above are the starting values.
- **Milestone 0 builds two rules as a stub,** in `meridian_oauth_radar.rules`. Each marks a grant high on its own, and their points only set the order:

  | Rule id | Fires when | Points |
  |---|---|---|
  | `broad_scope` | The grant holds full Gmail (`https://mail.google.com/`), `gmail.modify`, `gmail.readonly`, full Drive (`drive`) or `drive.readonly` | 3 |
  | `banned_app` | The grant's client ID is on IT's banned list | 5 |

  Findings are ranked by score, then app name, user and app ID, so ties always come out in the same order.

**Consequences:**

- Every finding is explainable and every score can be recomputed by hand.
- The dataset in milestone 1 knows what to plant, and the answer key knows what counts as found.
- Rules miss what nobody wrote a rule for, such as an AI app with a plain name, no catalogue entry and narrow scopes. "Not reviewed" and "unknown scope counts as broad" are the safety nets. The sealed held-out world is the test of whether they're enough.
- Leaning toward flagging will cost precision. It's reported, not hidden.
- The starting points are a judgement, not data. Milestone 2 may change them on the practice world, by a pull request that says why.
