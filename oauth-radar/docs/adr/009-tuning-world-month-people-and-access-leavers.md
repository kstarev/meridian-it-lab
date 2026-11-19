# OAR-009: The tuning world: grants for the month roster's people, leavers from the Access world's HRIS

**Date:** 2026-11-19 · **Status:** Accepted (owner, 2026-11-19). Supersedes [OAR-003](003-grants-enter-the-shared-dataset.md).

**Context:** OAR-003 proposed reading the OAuth world's people, with their HRIS leavers, from the Access-as-Code tuning world. The owner's catalogue says to "reuse the shared Meridian people, not a separate company" (R15), and milestone 1 used the 230 people of `meridian_dataset.month.personas` instead, which have no leaver status ([catalogue-differences.md](../catalogue-differences.md), rows 16 to 18). On 2026-11-19 the owner accepted the month roster and ruled that leavers are read from the Access world's HRIS records, and that the plants gain a banned-app grant and a leaver. The two rosters share no one: no work email of the month roster is in the Access tuning world's HRIS.

**Decision:**

- **Its own module, `meridian_dataset.oauth`,** in the shared dataset: its own generator version, its own seed (9137, used by no other world), its own `random.Random(seed)`, its own files and answer key. A tuning world only; no held-out or sealed OAuth world exists ([OAR-006](006-practice-and-sealed-held-out-worlds.md) waits).
- **The people:** the 230 of the month roster (`build_personas()`), read only.
- **Leavers come from the Access world's HRIS records:** the Access tuning world, built in memory with `generate_access()` (seed 6108, the public tuning recipe), read only. The OAuth world copies the few HRIS fields the radar needs (employee ID, work email, status, end date) for the people HRIS shows as gone by the snapshot. A leaver whose Google account was never removed is in the directory beside the 230, with the grant they left behind. The pull request that builds it gives the file and the details.
- **What is planted:** the catalogue's 6 risky grants, plus a grant to an app on IT's banned list and a leaver's grant. Each is marked only in the answer key, which is written apart from everything the radar reads, and look-alike decoys stay so precision means something.
- **Nothing outside the OAuth world moves.** `generate_access()`, `build_personas()` and every other generator are unchanged, and nothing is written into another world's folder. tier0's worlds, the Access tuning world's golden manifest, the month roster and every sealed world keep their hashes; tests hold this.
- **Tests:** the OAuth world's own golden hash, and the same seed gives the same bytes; the month roster is unchanged; building the OAuth world leaves the Access tuning world's bytes unchanged; the planted counts match the answer key; nothing the radar reads names a plant.
- **Rejected:** moving the OAuth world's people to the Access world (it undoes the accepted month roster); inventing leaver status for the month roster (it would make up HRIS records instead of reading them).

**Consequences:**

- The leaver override is exercised by data from the HRIS the lab already has, without moving any pinned world.
- The OAuth world now depends on the Access generator. If that generator changes on purpose, the OAuth golden hash moves too, in the same pull request.
- The two rosters still don't match: the month roster's people have no HRIS records, and the Access world's current staff hold no grants here. Only a leaver links them.
