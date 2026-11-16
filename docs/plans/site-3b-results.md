# Site: milestone 3b results on the project page

**Date:** 2026-11-15 · **Branch:** `site-3b-results` · **Files:** `site/index.html`, `tier0/tests/test_site_numbers.py`

Written overnight, unattended. The owner was not asked; each decision is recorded here so it can be reversed.

## What changed on the page

- **Results section:** a new block, "The follow-up test", directly under the held-out v3 chart and its caption. The v3 chart and its text are unchanged.
- **One caption in the v3 cards:** "Milestone 2 vs the final version" now reads "Milestone 2 vs the version in the chart below".
- **What's next:** the card "Fix the new-starter case" is gone. A new card, "Measure passed-on requests", replaces it, and it sits second, after the guides card.

## Decisions

1. **Wording comes from the README section "The follow-up test".** The owner already accepted it, so the page reuses its sentences. Where the page differs, it says less, not more.
2. **Three small tables, one per test, instead of the README's one wide table.** Rows are the three versions, columns are Haiku and Sonnet. Three columns fit a phone without sideways scrolling; six do not. They are real tables with a caption, column headers and row headers. Nothing is shown by colour alone.
3. **The numbers are the README table's numbers.** The test builds the page's cells from `paired_summary.readme_block`, so the page and the README cannot drift apart.
4. **The setups are called "Haiku" and "Sonnet"** in the block, as in the page's cost card. The README's lowercase "haiku" is the setup's code name.
5. **A note under the third table: "The right answer here is a refusal."** Without it, "12 of 12" requests for a colleague's login could be read as 12 logins given out.
6. **No intervals on the page.** The README section has none. The page says "too small a difference to say the fix improved the rate", as the README does. A test keeps the report's corrected phrase ("inside each other's intervals") off the page.
7. **"Its other miss in the last column" became "Its other miss with the fix".** The page's tables have rows per version, so there is no last column.
8. **One added sentence about cost.** The page's cost card shows $0.08 and $1.66 per 1,000 requests, from held-out v3 with the old instructions. The new instructions are kept (ADR-039) and cost more: $0.09 and $2.16 on v4. The card is left as it is, because it matches the v3 chart beside it. The follow-up block says what the new instructions cost. The sentence does not say why they cost more: the numbers file cannot show that the prompts are longer.
9. **"the final version" changed to "the version in the chart below".** After 3b the version tested on v3 is no longer the latest one, and the follow-up calls it "the old one". "The final run" is kept everywhere: it is still true of each sealed set.
10. **The new what's-next card is second, not first.** The brief's order is 3b, then milestone 4 (the guides), then 3d, whose sealed set measures passed-on requests. Putting the card first would show the wrong order. The guides card's text is unchanged; only its number is now 01.
11. **The card says "a new locked-away test set", not "the next".** The next sealed set is milestone 4's how-to test; 3d's comes after it (ADR-039). The caveat in the Results block keeps the README's "The next test set will include them".
12. **Left as they are:** the four key numbers under the hero (still true of held-out v3; "0 unsafe actions" also holds for every 3b run, and a test now checks that), "39 decision records", and "1,157 automated tests" (the suite has more, and the page may claim fewer).

## Tests

`tier0/tests/test_site_numbers.py` has 10 new tests. Each new sentence is rebuilt from `docs/evals/2026-11-15-m3b-held-out-v4-v4b.json` and required word for word. Two sentences are not numbers and are checked another way:

- "one request from a new starter or intern ... The fault was in a code check, not in the AI" is checked against the held-out v3 reports, like the README's sentence.
- "Not measured yet" and the what's-next card are checked against `docs/brief.md`, which says where passed-on requests get measured.

One more test reads every text node with a digit in the follow-up block and fails if any of them is not one the other tests rebuild.

## Not done here, for the owner

- **The README's "What's next" list is out of date.** Item 1 still says the new-starter fix "will be checked on a new set of test requests". This PR may not touch the README.
- **`og.png` was not touched.**
- **The site was not deployed.** The Pages workflow runs only on a push to `main`.
