# AAC-009: The held-out recipe can bring new kinds of mess, written blind and sealed out of the builder's reach

**Date:** 2026-11-14 · **Status:** Accepted. Refines [AAC-005](005-sealed-answer-key.md).

**Context:** AAC-005 seals a held-out recipe written by a blind helper. If that recipe could only change counts and the seed, the held-out world would have exactly the kinds of mess the tuning world has, and a builder could pass it by handling only those: the four column layouts, the four date formats, the three username schemes, the known HR cases. The user asked for a held-out world that can bring mess the practice world doesn't have.

**Decision:**

- **Recipe format 2 describes the world as data.** The generator applies whatever the recipe says:
  - column headers, delimiters, preamble and footer lines, and role and status labels;
  - date formats (any `strftime` pattern, Excel serial numbers, Unix time);
  - extra name spellings and display styles;
  - username patterns built from placeholders;
  - file encodings, and row noise such as duplicate rows;
  - **custom HR cases**: people, HRIS events, accounts and the expected findings, written by the author.

  The format is documented in `dataset/ACCESS_RECIPE.md`.
- **What to do stays a fixed, public vocabulary:** the finding types (part 1's nine plus `hr_data_error`) and the expected actions. That vocabulary is the product's spec. What the world looks like is open.
- **Written blind.** A helper agent writes the held-out recipe, `held_out_a1`. It reads only `dataset/ACCESS_RECIPE.md`, `python -m meridian_dataset.access.recipe_tools catalog` and the public tuning recipe, and never opens Access-as-Code code, the generator or the tests. It checks its recipe with `recipe_tools check` and seals it with `recipe_tools seal`.
- **Sealed and out of the builder's reach:**
  - The recipe is stored as `recipes/held_out_a1.json.gz`, compressed, so a search or a file read shows nothing readable.
  - Its SHA-256 is pinned in the code, and a test fails if it changes.
  - The builder never decompresses or reads it.
  - Tests on it are count-only, and the novelty checks report only yes or no, never contents.
  - The world is built only with `--final-score=held_out_a1`.
- **The key is checked three ways before it counts:**
  - **Code checks** (`meridian_dataset.access.key_checks`, run by `recipe_tools check` and `seal` and by the tests) look at references, duplicates and contradictions, per-type counts against the recipe and its cases, and completeness from the world's own data. They report counts only, so they can run on the sealed world.
  - **A second blind reviewer**, which has seen neither the Access-as-Code code nor the author's notes, reads the recipe and the world it builds. It judges whether the key is right and reports only its problem counts per type and PASS or FAIL. Its detailed notes go to the author, never to the builder. If it fails, the author fixes the recipe and seals it again, and the review is repeated.
  - **A blind referee** ([AAC-011](011-blind-referee-against-shared-mistakes.md)) builds its own key from the raw files, and every disagreement with the generator's key goes to the owner for a ruling. It is the check against mistakes the generator and the tool share.
- **Novelty is checked, not hoped for.** Tests require, beyond the tuning recipe:

- at least 2 layouts with new headers;
- 2 new date formats, 1 of them special;
- 2 new username patterns;
- new name spellings;
- a new encoding;
- duplicate rows;
- at least 5 custom HR cases, each with findings.

They report yes or no only.
- **tier0's four worlds stay byte-identical** (`test_tier0_worlds_unchanged.py`).

**Consequences:**

- A builder can't pass the final score by handling only the mess it has seen.
- The generator's vocabulary limits the novelty: systems, fields, event kinds, finding types and actions are fixed. A kind of problem nobody described in that vocabulary can't appear (an honest limit in the brief).
- "Out of reach" is a convention backed by compression, a pinned hash and tests, not encryption. It relies on the builder not decompressing the file, as tier0's sealed sets rely on nobody reading them.
- Changing the recipe means a new helper run and a new seal, under a new name.
