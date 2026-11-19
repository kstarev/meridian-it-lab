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
