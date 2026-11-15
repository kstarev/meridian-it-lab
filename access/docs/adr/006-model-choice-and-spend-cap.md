# AAC-006: The model interprets, code enforces; Haiku, Sonnet and Opus 5.5 compared on tuning data; a $3 cap

**Date:** 2026-11-14 · **Status:** Accepted; the comparison is done and the model is picked: Haiku 5.5 (2026-11-15, see "The pick" below). The comparison and the cap were the user's choice (2026-11-14). Follows lab ADR-028, ADR-030, ADR-032 and ADR-034. The $3 cap is replaced by lab ADR-037's per-item budget (2026-11-15): Access-as-Code has $30, in three sub-capped items.

**Context:** Three jobs in Access-as-Code are hard for rules: reading messy job titles, matching accounts that rules can't place, and explaining a plan in plain English. None of them should let a model decide access. tier0 showed the pattern that works: the model classifies, code acts and speaks (lab ADR-028), with every paid call checked against a spend cap (lab ADR-032).

**Decision:**

- **The model does three things only:**
  1. **Job title → role suggestions.** Input: the title, department and team. Output: one role from the policy's list, or "none", via structured outputs (a JSON schema in `output_config.format`) so code can validate it. A suggestion becomes a map entry marked `source: ai`, in the plan pull request, for a person to approve. An unmapped title gets only the apps everyone gets.
  2. **Identity match suggestions,** only for accounts the rules left unmatched (exact email, normalised email, known aliases, username → directory). Output: a person ID or "no match", with the evidence fields it used. Same path: a reviewed map entry first, and a change based on it only in a later plan.
  3. **The plain-English summary** of a plan code already built. Code writes the table of counts; the model writes the paragraph. Code checks every name, app and number in the paragraph against `plan.json` and replaces it with a template summary if anything doesn't match.
- **The model never** decides access, sees a token or calls an API. It gets only the fields each job needs: no personal email addresses, no HRIS dates beyond what a title needs.
- **Candidates:** Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 (`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`). In milestone 4 each is measured on the **tuning world only**: title-mapping accuracy, matching precision and recall on the rules' leftovers, summary pass rate on the code check, and cost. The user picks one using those numbers, as lab ADR-030 did for tier0. Prompts are then frozen by hash before the held-out score.
- **The pick: Claude Haiku 5.5** (`claude-haiku-5-5`), the owner's decision on 2026-11-15. The rule was set before the run: best score first, then cost; a tie within the interval goes to the cheapest. In the comparison at k=3 on the tuning world ([report](../../../docs/evals/2026-11-15-access-model-comparison-k3.md)) all three models got 75 of 76 job titles right in every repeat. On the leftover accounts Sonnet and Opus made 5 right matches of 5 and Haiku 4 of 5. That is a tie within the 95% intervals, and Haiku costs about a cent per run, against $0.22 for Sonnet and $0.45 for Opus. The code reads the pick from `CHOSEN_MODEL` in `meridian_access.ai.pricing`.
  - **Known limit, to re-check on the final held-out score (milestone 7):** Haiku's one miss is a false match, and it made the same one in all three repeats, so it is not run-to-run noise. A false match is worse than no match. It reaches nothing without a person's review, but if Haiku makes false matches on the held-out world too, the pick is reopened.
  - **Not measured:** the comparison scored job titles and account matches only, not the plain-English summary. The summary now defaults to the chosen model as well (`ACCESS_SUMMARY_MODEL` overrides it). It has never run live, and code replaces it with the template if any name, app or number doesn't match. Its pass rate is to be measured before it is turned on.
  - The budget item `aac-model-comparison` is set back to unapproved: it spent $2.02 of its $12 cap, and the rest can't be spent without a new approval.
- **A refusal, a malformed answer or an API error** counts as "no suggestion", never as a match.
- **Spend cap:** its own ledger and cap, separate from tier0's: `ACCESS_CLAUDE_SPEND_CAP_USD`, **$3 by default**. Every call is checked against the cap first, at its worst-case cost, so the cap is never crossed (the design of tier0's ledger, lab ADR-032). The nightly job also has a per-run limit; when either is reached, the summary falls back to the template and the plan still opens.
- **Where it runs:** paid runs happen only on GitHub Actions ([AAC-008](008-runs-on-github-only.md)). Model comparisons and the final score run by hand (`workflow_dispatch`) in an environment that needs the owner's approval. The API key is an Actions secret in that environment. The ledger is kept on the `access-state` branch so the cap holds across runs.
- **No local models.** Ollama was tier0's benchmark; here production and evaluation both use the hosted model (lab ADR-034).

**Consequences:**

- Every access decision can be explained without the model: a reviewed map entry, a policy line and an HRIS field.
- AI help is measured, not assumed: rules only vs rules + AI is a headline result.
- A suggestion takes a day longer to have an effect, because it's reviewed before anything depends on it.
- If the cap is reached, the project keeps working; it only loses the AI's help until the cap is raised.
