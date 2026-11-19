# Milestone 1: the radar's first working loop

**Plan of 2026-11-19.** The design comes from the owner's catalogue; `docs/catalogue-differences.md` (its own pull request) lists where it differs from the scaffold and OAR-001 to OAR-007. Every piece is code only: no AI, no model call, no network, nothing paid, fake data only. One pull request per piece:

1. Collector: a mock Google Admin token API and a collector that pages through it.
2. OAuth grants for the shared Meridian people, as a tuning world.
3. Scope explainer.
4. Risk rubric from OAR-002 (since 2026-11-19: OAR-008, accepted by the owner).
5. Dry-run policy engine.
6. Slack nudge drafts as files.
7. Weekly report.
8. Precision and recall on the tuning world.

## 1. Collector

[`fake_admin.py`](../src/meridian_oauth_radar/fake_admin.py) is the mock: an in-process `FakeAdminApi` with three read calls, shaped like the Admin SDK Directory API's `users.list` and `tokens.list` and the Reports API's token `activities.list` (the mock activity source for "last used"). It has no call that changes anything, holds no credentials and opens no socket.

[`collector.py`](../src/meridian_oauth_radar/collector.py) pages through the users, asks for each user's tokens, pages through the token activity, and returns a `Collection`: every user it asked about and every grant as a typed `TokenGrant` (user, client ID, display text, scopes, anonymous, native app, last used). Same data, same collection, whatever the page size or order. A page of the wrong shape stops the collection and says where.

Evidence: [`test_collector.py`](../tests/test_collector.py), including `test_no_socket_is_opened` (every socket call fails during the collection) and `test_the_fake_has_no_call_that_changes_anything`.

**Update, 2026-11-19:** on the owner's ruling ([OAR-011](adr/011-mock-admin-api-collector.md)), "last used" counts only use after the grant: the `authorize` event that made the grant isn't use, and neither is a `revoke`. A grant nobody has used since it was granted has no last-used time.

**Honest limits.** The mock is shaped like the real APIs for the fields the radar reads, not copied from them: real responses have more fields, rate limits, errors and quirks. "Last used" is the newest use the mock holds; a real audit log keeps only so much history.

## 2. OAuth grants for the shared Meridian people

[`meridian_dataset.oauth`](../../dataset/src/meridian_dataset/oauth/__init__.py) is a new module of the shared dataset. It gives the 230 shared Meridian people (`meridian_dataset.month.personas.build_personas()`, read only) Google OAuth grants to 19 made-up apps, from its own seed (9137) and random stream, as a snapshot on 2026-11-16. It is a **tuning world** only. `make oauth-data` writes it to `out/oauth-radar/tuning/`:

- `radar/`: what the radar may read: the directory's users, every user's tokens (`tokens.list` items), the token activity (for "last used"), a vendor directory with Google's verified flag, IT's app catalogue, IT's banned list, Security's list of compromised apps, and IT's exceptions with expiry dates.
- `key/answer_key.json`: **exactly 6 planted risky grants** (two old, unused grants with a restricted scope; two risky grants in daily use; one to an app Security lists as compromised; one that can change mail forwarding), plus look-alike decoys. The radar never reads it.

Evidence: [`test_oauth_world.py`](../../dataset/tests/test_oauth_world.py): same seed, same bytes (a golden manifest hash); the people are the shared 230 and unchanged; exactly 6 plants, each by a different person; nothing under `radar/` names a plant; fake data only, no token values. No existing world or pinned digest moves, and their tests are unedited.

**Update, 2026-11-19:** on the owner's ruling ([OAR-009](adr/009-tuning-world-month-people-and-access-leavers.md)), leavers are read from the Access tuning world's HRIS records (`generate_access()`, seed 6108, read only): `radar/hris_leavers.json` holds four fields (employee ID, work email, status, end date) of everyone HRIS shows as gone before the snapshot. The month roster and the Access HRIS share no person, so the leaver is one of those HRIS people whose Google account was never removed, added to the directory beside the 230. Two plants join the 6: a grant to the app IT banned, still in use, and that leaver's grant to the meetings app (medium on its points alone, so only the leaver override makes it high). Both are drawn after everything else, and a test pins that the earlier grants don't move. OAuth generator 0.3.0. No pinned digest outside the OAuth world moves; a test checks the Access world's bytes are unchanged.

**Honest limits.** One builder wrote the world, the plants and (later) the rubric, so a shared misreading can't be caught here. A tuning world proves little on its own: it's for building and tuning, and its numbers are reported as tuning numbers. The people have no leaver status, so nothing exercises a leaver rule.

## 3. Scope explainer

[`scopes.py`](../src/meridian_oauth_radar/scopes.py) is a static, reviewed table from each Google OAuth scope URL to one plain-English sentence of what it lets an app do ("This app can read, send and delete all your email, and change your mail settings."), the data it reaches, and Google's class for it (restricted, sensitive, non-sensitive, or admin). An unknown scope is never guessed at: the explainer says plainly that it has no description and that IT should check it, and its class is "unknown".

Evidence: [`test_scopes.py`](../tests/test_scopes.py): the catalogue's examples, an unknown scope, every entry one short sentence, and every scope the tuning world holds is in the table.

**Honest limits.** The classes follow Google's restricted and sensitive scope lists as recalled on 2026-11-19, without a network to check them; a person should check them against Google's current lists. The table covers the common Workspace scopes, not all of them.

## 4. Risk rubric from OAR-002

**Update, 2026-11-19:** the owner accepted the rubric with two changes, recorded in [OAR-008](adr/008-risk-rubric-with-the-owners-changes.md) and built in `rubric.py`: an app IT approved is never high because of its reach alone (it is medium, and the score says why), and the AI signal is the vendor directory's AI flag, with the word list only for apps the directory doesn't know. IT's own AI tag no longer counts. The text below is as milestone 1 built it.

**Proposed (OAR-002), not yet accepted by the owner.** [`rubric.py`](../src/meridian_oauth_radar/rubric.py) builds OAR-002's rubric exactly as [OAR-002](adr/002-risk-model-rules-first.md) states it: scope breadth 3/2/0 (the broadest scope, not the sum; an unknown scope counts as broad), unverified publisher +2, reach +1 for 5 to 24 people and +2 for 25 or more, AI app +1, not reviewed by IT +1; a banned app or a leaver's grant above sign-in is always high; 5 points or more is high, 3 to 4 medium, 2 or fewer low. Every score lists all five signals with their points and a plain reason, so it can be recomputed by hand. The catalogue's own rubric (scope sensitivity times users times dormancy times vendor maturity) is not built; that is the owner's call (R14).

[`enrich.py`](../src/meridian_oauth_radar/enrich.py) is the enricher: from the vendor directory, IT's catalogue and the banned list it gives each app's vendor, category, verified flag, data location, vendor maturity, IT's review and AI tag, and the approved alternative (an app IT approved in the same category).

How the scaffold's words were read, where OAR-002 leaves a choice: broad means Google's restricted scopes and Admin SDK scopes, medium means Google's sensitive scopes, narrow means non-sensitive scopes, from the scope explainer's classes. A publisher nobody knows counts as not verified. Reach counts people holding a grant to the app (the mock has no admin installs). The AI word list is `ai`, `gpt`, `llm`, `copilot`, `assistant`, `agent`, `transcribe` and `transcription`, as whole words, used only for apps IT never reviewed. Each choice is logged for the owner.

Evidence: [`test_rubric.py`](../tests/test_rubric.py): OAR-002's four worked examples, points per signal on every score, unknown scopes, the reach steps, the AI word list, both overrides, a ranking that doesn't depend on input order, and the enricher on the tuning world.

## 5. Dry-run policy engine

[`policy.py`](../src/meridian_oauth_radar/policy.py) applies the catalogue's policy as plans and drafts only. On a simulated date, for each scored grant: an app on Security's compromised list goes into an **emergency plan** that a person must trigger; a grant under a current exception is left alone; a grant unused for more than 90 days that holds a restricted scope gets a **notice draft** first and, once the notice is 7 days old and the grant still unused, goes into the **after-notice plan**; a restricted grant with no record of use, and every high or medium grant, goes to a person; low grants need nothing. A plan over the **blast-radius limit** (10 grants) is refused whole and its grants go to a person. Every plan says `"dry_run": true` and `"applied": false`, and names the action "revoke" only as data, with how a person would do it by hand. The same inputs and date give byte-identical files, and a notice is never issued twice; the notice ledger carries over to the next run.

Evidence: [`test_policy.py`](../tests/test_policy.py): notice, waiting and plan over a simulated week; a grant used after its notice; exceptions before and after expiry; the emergency plan; the blast-radius limit; idempotent files; the tuning world over a simulated week.

**Update, 2026-11-19:** on the owner's ruling ([OAR-010](adr/010-dry-run-removal-plans-never-applied.md)), an exception lasts at most 90 days, from its approval date (a new field, `approved_on`) to its expiry date. One with a longer term, no expiry or no approval date is refused when the list is loaded, never trimmed; it protects nothing, and `run.json`, the weekly report and the command line list it. The tuning world has one such entry, written for a whole year.

**Honest limits.** Nothing applies a plan, so "removed" in any number means "in a reviewed plan". The notice is a draft file; nobody is told anything. The 90 days, 7 days and 10 grants are settings chosen for the tuning world's scale, not measured.

## 6. Slack nudge drafts as files

[`nudges.py`](../src/meridian_oauth_radar/nudges.py) writes one Slack message draft per user, as a Markdown file, for risky apps people still use: grants the policy sent to a person that the rubric rates high, used within the last 90 days, to an app IT hasn't approved. The message says what the app can do, in the scope explainer's words, and names the approved tool in the same category ("*Draftmoth AI Writer* is connected to your Google account. This app can read, change and send all your email (but not delete it for good). Here's the approved tool that does the same thing: *Vellumine Assist*."), or says IT has no approved tool for it yet. Nothing is sent: the package holds no Slack client, and the guard test refuses Slack libraries.

Evidence: [`test_nudges.py`](../tests/test_nudges.py).

**Honest limits.** "Risky" is the OAR-002 rubric's "high", so the nudges inherit its false alarms. The catalogue's "users who switched after a nudge" needs simulated responses, which aren't built.

## 7. Weekly report

[`run.py`](../src/meridian_oauth_radar/run.py) is one dry run: collect from the mock API, score, apply the policy on a simulated date, draft nudges, and write the run's files (`run.json`, `scores.json`, the two plans, notice drafts and their ledger, the list for a person, nudge drafts). [`report.py`](../src/meridian_oauth_radar/report.py) builds the weekly Markdown report for IT and Security **from those files alone**: the counts, the emergency plan, the after-notice plan, what a person should review (grouped by app, not by person), open notices, nudge drafts, and every app with its points per signal, vendor, data location and IT status. `make oauth-run` writes the tuning world and a run into `out/oauth-radar/`; `--today` and `--ledger` simulate a later week.

Evidence: [`test_run_report.py`](../tests/test_run_report.py): the same inputs give the same files and leave the inputs unchanged; the report rebuilt from the written files is identical; a week later the noticed grants are in the plan; the run works with the answer key nowhere on disk.

## 8. Precision and recall on the tuning world

[`evaluate.py`](../src/meridian_oauth_radar/evaluate.py) is the grader, and the only module that reads an answer key. It runs the radar on the tuning world on its snapshot date and again a week later, then counts, against the 6 planted risky grants, how many are found and how many false alarms there are, two ways: **marked high by the rubric** (OAR-002's own definition of found) and **acted on by the radar** (a notice, a plan, or a nudge draft). Every rate has its count and a 95% Wilson interval. It also reports the catalogue's "dormant grants removed (simulation)": grants in the after-notice plan a week later, out of all grants unused for more than 90 days.

The numbers are in the generated report, [docs/evals/2026-11-19-oauth-radar-tuning.md](../../docs/evals/2026-11-19-oauth-radar-tuning.md), not typed here. They are tuning numbers: one builder wrote the world, the plants and the radar.

**Update, 2026-11-19:** on the owner's ruling (R16, [OAR-008](adr/008-risk-rubric-with-the-owners-changes.md)), the headline is "acted on by the radar", with the lab brief's targets: precision at least 0.90 and recall 100%, each reported as PASS or FAIL and a FAIL until met. "Marked high by the rubric" is the second line, reported without a target. Tests pin both.

Evidence: [`test_evaluate.py`](../tests/test_evaluate.py) pins every count, checks the published report is exactly the generated one, and checks no other module names an answer-key field.
