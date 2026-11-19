# OAR-003: OAuth grants join the shared dataset in their own module, and no existing world moves

**Date:** 2026-11-15 · **Status:** Superseded by [OAR-009](009-tuning-world-month-people-and-access-leavers.md) (owner, 2026-11-19). Before that it was Proposed and never accepted; the text below is unchanged. Its original note: Builds on lab ADR-001 and follows [AAC-004](../../../access/docs/adr/004-extend-the-shared-dataset.md). Nothing in `dataset/` changes in milestone 0.

**Context:** Lab ADR-001 says the one shared dataset holds OAuth grants, with risky grants among its planted problems. No generator writes them yet. The worlds that exist must stay byte-identical, because published and sealed results rest on them:

- tier0's six worlds (tuning, v1, v2, v3, v4, v4b), checked by `dataset/tests/test_tier0_worlds_unchanged.py`;
- the Access-as-Code tuning world, checked by its golden manifest hash in `dataset/tests/test_access_world.py`;
- Access-as-Code's sealed held-out world, checked by its seal.

The radar also needs the people of a world, not only grants: a "leaver grant" only means something if HR says the person has left.

**Decision:**

- **Milestone 0 changes nothing in `dataset/`.** Its fixtures are hand-written, tiny and fake, in `oauth-radar/tests/fixtures/`.
- **Milestone 1 adds a new module, `meridian_dataset.oauth`,** in the shared package, with:
  - its own generator version, starting at 0.1.0;
  - its own seeds, never one of tier0's or Access-as-Code's;
  - its own `random.Random(seed)`, so its draws can't shift anyone else's;
  - its own recipe files and answer key.
- **It reads its people from an Access-as-Code world, and adds nothing to it.**
  - The practice world reads the Access tuning world, built in memory with `generate_access()` (seed 6108, the public tuning recipe). It reads the HRIS records (names, work emails, status, end dates) and the Google and Slack user lists, so leavers and usernames line up with the same company.
  - The world objects are frozen dataclasses. The module only reads them. It never calls `write_access`, never writes into an access world's folder, and adds no field, record or file to any existing output. `generate()` and `generate_access()` don't change.
  - The held-out world reads an access world built from the public tuning recipe on a new seed that the blind writer picks, so its people are new too ([OAR-006](006-practice-and-sealed-held-out-worlds.md)). It never reads Access-as-Code's sealed recipe.
- **It writes to its own folder,** `out/oauth-radar/seed-<N>/`, with its own `manifest.json`:
  - the export-shaped files of [OAR-005](005-offline-export-shaped-files.md);
  - `people.json`, the few HRIS fields the radar needs (employee ID, work email, status, end date), copied from the access world so the folder stands alone;
  - `answer_key.json`, kept apart from everything the radar reads. A test fails if any loader input holds an answer-key field, as Access-as-Code does.
- **Tests that hold this, from milestone 1:**
  - The tier0 hash test and the Access world tests stay as they are, unedited, and pass.
  - A new test hashes the Access tuning world's manifest, builds the OAuth world from it, then builds and hashes the Access world again: both hashes must equal the Access golden hash.
  - The OAuth world gets its own golden hash, and the same seed gives the same bytes.
  - The planted counts match the recipe, checked from the answer key.
- **Rejected:**
  - Adding grants to the Access world: its golden hash and its sealed world's seal would move.
  - Adding grants to tier0's world: six hashes would move, and its 10 people are too few.
  - A separate company of its own: leavers and usernames wouldn't line up with the same Meridian, against lab ADR-001.

**Consequences:**

- All three projects describe one company, so their results can sit side by side.
- No existing world can move by accident: tests guard it, not care.
- The OAuth world depends on the Access generator. If that generator changes on purpose, the OAuth golden hash moves too, and both changes show up in the same pull request.
- The dataset package grows by one module and gets one more section in its docs.
