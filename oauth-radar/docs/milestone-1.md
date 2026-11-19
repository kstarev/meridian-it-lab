# Milestone 1: the radar's first working loop

**Plan of 2026-11-19.** The design comes from the owner's catalogue; `docs/catalogue-differences.md` (its own pull request) lists where it differs from the scaffold and OAR-001 to OAR-007. Every piece is code only: no AI, no model call, no network, nothing paid, fake data only. One pull request per piece:

1. Collector: a mock Google Admin token API and a collector that pages through it.
2. OAuth grants for the shared Meridian people, as a tuning world.
3. Scope explainer.
4. Risk rubric from OAR-002 (Proposed, not yet accepted by the owner).
5. Dry-run policy engine.
6. Slack nudge drafts as files.
7. Weekly report.
8. Precision and recall on the tuning world.

## 1. Collector

[`fake_admin.py`](../src/meridian_oauth_radar/fake_admin.py) is the mock: an in-process `FakeAdminApi` with three read calls, shaped like the Admin SDK Directory API's `users.list` and `tokens.list` and the Reports API's token `activities.list` (the mock activity source for "last used"). It has no call that changes anything, holds no credentials and opens no socket.

[`collector.py`](../src/meridian_oauth_radar/collector.py) pages through the users, asks for each user's tokens, pages through the token activity, and returns a `Collection`: every user it asked about and every grant as a typed `TokenGrant` (user, client ID, display text, scopes, anonymous, native app, last used). Same data, same collection, whatever the page size or order. A page of the wrong shape stops the collection and says where.

Evidence: [`test_collector.py`](../tests/test_collector.py), including `test_no_socket_is_opened` (every socket call fails during the collection) and `test_the_fake_has_no_call_that_changes_anything`.

**Honest limits.** The mock is shaped like the real APIs for the fields the radar reads, not copied from them: real responses have more fields, rate limits, errors and quirks. "Last used" is the newest token activity the mock holds; a real audit log keeps only so much history.

## 2. OAuth grants for the shared Meridian people

[`meridian_dataset.oauth`](../../dataset/src/meridian_dataset/oauth/__init__.py) is a new module of the shared dataset. It gives the 230 shared Meridian people (`meridian_dataset.month.personas.build_personas()`, read only) Google OAuth grants to 19 made-up apps, from its own seed (9137) and random stream, as a snapshot on 2026-11-16. It is a **tuning world** only. `make oauth-data` writes it to `out/oauth-radar/tuning/`:

- `radar/`: what the radar may read: the directory's users, every user's tokens (`tokens.list` items), the token activity (for "last used"), a vendor directory with Google's verified flag, IT's app catalogue, IT's banned list, Security's list of compromised apps, and IT's exceptions with expiry dates.
- `key/answer_key.json`: **exactly 6 planted risky grants** (two old, unused grants with a restricted scope; two risky grants in daily use; one to an app Security lists as compromised; one that can change mail forwarding), plus look-alike decoys. The radar never reads it.

Evidence: [`test_oauth_world.py`](../../dataset/tests/test_oauth_world.py): same seed, same bytes (a golden manifest hash); the people are the shared 230 and unchanged; exactly 6 plants, each by a different person; nothing under `radar/` names a plant; fake data only, no token values. No existing world or pinned digest moves, and their tests are unedited.

**Honest limits.** One builder wrote the world, the plants and (later) the rubric, so a shared misreading can't be caught here. A tuning world proves little on its own: it's for building and tuning, and its numbers are reported as tuning numbers. The people have no leaver status, so nothing exercises a leaver rule.

## 3. Scope explainer

[`scopes.py`](../src/meridian_oauth_radar/scopes.py) is a static, reviewed table from each Google OAuth scope URL to one plain-English sentence of what it lets an app do ("This app can read, send and delete all your email, and change your mail settings."), the data it reaches, and Google's class for it (restricted, sensitive, non-sensitive, or admin). An unknown scope is never guessed at: the explainer says plainly that it has no description and that IT should check it, and its class is "unknown".

Evidence: [`test_scopes.py`](../tests/test_scopes.py): the catalogue's examples, an unknown scope, every entry one short sentence, and every scope the tuning world holds is in the table.

**Honest limits.** The classes follow Google's restricted and sensitive scope lists as recalled on 2026-11-19, without a network to check them; a person should check them against Google's current lists. The table covers the common Workspace scopes, not all of them.

## 4. Risk rubric from OAR-002

**Proposed (OAR-002), not yet accepted by the owner.** [`rubric.py`](../src/meridian_oauth_radar/rubric.py) builds OAR-002's rubric exactly as [OAR-002](adr/002-risk-model-rules-first.md) states it: scope breadth 3/2/0 (the broadest scope, not the sum; an unknown scope counts as broad), unverified publisher +2, reach +1 for 5 to 24 people and +2 for 25 or more, AI app +1, not reviewed by IT +1; a banned app or a leaver's grant above sign-in is always high; 5 points or more is high, 3 to 4 medium, 2 or fewer low. Every score lists all five signals with their points and a plain reason, so it can be recomputed by hand. The catalogue's own rubric (scope sensitivity times users times dormancy times vendor maturity) is not built; that is the owner's call (R14).

[`enrich.py`](../src/meridian_oauth_radar/enrich.py) is the enricher: from the vendor directory, IT's catalogue and the banned list it gives each app's vendor, category, verified flag, data location, vendor maturity, IT's review and AI tag, and the approved alternative (an app IT approved in the same category).

How the scaffold's words were read, where OAR-002 leaves a choice: broad means Google's restricted scopes and Admin SDK scopes, medium means Google's sensitive scopes, narrow means non-sensitive scopes, from the scope explainer's classes. A publisher nobody knows counts as not verified. Reach counts people holding a grant to the app (the mock has no admin installs). The AI word list is `ai`, `gpt`, `llm`, `copilot`, `assistant`, `agent`, `transcribe` and `transcription`, as whole words, used only for apps IT never reviewed. Each choice is logged for the owner.

Evidence: [`test_rubric.py`](../tests/test_rubric.py): OAR-002's four worked examples, points per signal on every score, unknown scopes, the reach steps, the AI word list, both overrides, a ranking that doesn't depend on input order, and the enricher on the tuning world.
