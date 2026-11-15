# AAC-011: A blind referee builds its own key from the raw held-out files, as the check against shared mistakes

**Date:** 2026-11-15 · **Status:** Accepted. Adds a third check to [AAC-009](009-held-out-recipe-can-bring-new-mess.md). The user's decision (2026-11-15).

**Context:** The held-out answer key comes from the generator, and the generator was written by the same builder who writes Access-as-Code. The code checks and the blind reviewer ([AAC-009](009-held-out-recipe-can-bring-new-mess.md)) catch a key that's inconsistent or that breaks the public rules. Neither catches a misreading of the rules that the builder put into both the generator and the tool. Such a mistake would show up as the tool agreeing with the key.

**Decision:**

- **A new blind helper writes a referee script.** It has never seen the generator, Access-as-Code, the tests or any earlier helper's notes. It reads only:
  - the raw held-out files (the HR export, the Okta, Google and Slack data, and the 95 app exports);
  - the public guide (`dataset/ACCESS_RECIPE.md`, including the access policy table, and the `recipe_tools catalog` output).

  From those it builds its own answer key by rules, not by a typed-in list.
- **The referee is sealed like the recipe.** It's stored as `recipes/held_out_a1_referee.py.gz`, its SHA-256 is pinned, and the builder never reads it. Before it runs, a check reports how many of its imports fall outside a standard-library allowlist, how many forbidden names it uses (`eval`, `exec`, `getattr` and the like) and how many string constants look like paths or key files. The check gives counts only, and all three must be 0. It is a guard against accidents, not a security boundary: it counts imports, names and strings, and a determined script could get around it. What keeps the key out of reach is that no answer key exists on disk during the run. The referee runs in its own process, with a stripped environment, in a temp directory that holds only a copy of the raw files.
- **The comparison reports counts per problem type only:** what the generator's key has, what the referee has, how many agree, how many differ on the same account, and how many each side has alone. `python -m meridian_dataset.access.referee compare --final-score held_out_a1` prints the table. With `--details PATH`, outside the repository, it also writes the disagreements for the owner.
- **Every disagreement goes to the owner for a ruling, logged, before any held-out number is published.** The log is `docs/evals/2026-11-15-access-held-out-a1-referee.md`. It holds the count table, which a test keeps in step with the live comparison, and one ruling per disagreement id: the key is wrong, the referee is wrong, or both are acceptable. It holds no held-out content.
- **A key error is fixed by the recipe's author,** the first helper, who seals the recipe again under the same rules. The comparison is then re-run and the log updated. The builder never sees the content.

**Consequences:**

- A mistake shared by the generator and the tool now has to get past a third author, one who read only the data and the public rules.
- Where the referee is wrong, the owner's ruling says so, and the referee isn't changed to agree.
- The owner sees the disagreeing held-out items now, before the tool is built, not only at the end as [AAC-010](010-human-rulings-before-publishing.md) planned. The rulings reach the builder as ids and outcomes only, never as content.
- **The access policy is public.** The role → apps table the generator uses is an input, as a real IT team's access policy is, so it's in the public guide and the catalog (the user's decision, 2026-11-15). The referee and Access-as-Code read the same policy, and milestone 3's policy YAML is built from that table. No recipe field changes it, so publishing it reveals nothing about the held-out world.
- Some disagreements will be the referee's mistake, not the key's, and the ruling records which.
