# AAC-012: The reviewed job-title map is seeded from the practice world; new titles stay unmapped

**Date:** 2026-11-15 · **Status:** Accepted. Made overnight under the owner's "pick and log" rule; see the PR.

**Context:** Desired state needs each person's role, and HR's job titles are messy ("Sr. Acct Exec, EMEA"). The brief keeps a reviewed map, `access/policy/title_map.yaml`, from title to role. Somebody has to fill it. The generator's list of title spellings isn't public. Publishing it would hand over the answer to the title problem the AI step in milestone 4 is meant to help with.

**Decision:**

- **Seeded from the practice world only.** `python -m meridian_access.policy seed-titles` maps each distinct title in the tuning world's HR export to the role most tuning-world people with that title truly have. Ties go to the first role id in order. Entries are marked `source: tuning-review`. This stands in for the IT team reviewing the practice world's titles once.
- **A title not in the map is unmapped.** That person gets birthright apps only and is listed for a decision. In milestone 4 the AI suggests a role, as a map entry a person must approve ([AAC-006](006-model-choice-and-spend-cap.md)).
- **`--check` keeps the file in step** with the tuning world, in CI.

**Consequences:**

- On the practice world every current worker's title is mapped (195 of 195 to their true role), so practice-world role numbers are fitted and say so. The held-out world shows how the map holds up on titles nobody has reviewed.
- The map never sees held-out data. Held-out titles that also occur in the practice world are covered, and new ones fall to the unmapped path and the AI suggestions.
