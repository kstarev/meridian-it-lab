# AAC-013: Milestone 7's prompts are frozen by hash, the final run is guarded, and the owner's answers to M7-R1 to M7-R17

**Date:** 2026-11-16 · **Status:** Accepted (the owner's answers, 2026-11-16). Built overnight on 2026-11-15/16, with no paid call ([M7 prep](../m7-prep.md), PR #45). The final score waits for its budget item and the owner's go.

**Context:** Milestone 7 scores the AI steps once on the sealed held-out world (`held_out_a1`), with Haiku 5.5 ([AAC-006](006-model-choice-and-spend-cap.md)). A score taken once must be taken on exactly the prompts the tuning comparison measured, and the run must not be able to start by accident.

**Decision:**

- **The prompts are frozen by twelve SHA-256 pins and one freeze digest.**
  - The pins cover the system prompt, the user message, the schema and the whole request, for each of the three jobs (titles, owners, summaries).
  - User messages and schemas are hashed from fixed fake probe inputs.
  - The frozen client refuses any other model or prompt before the spend check, so nothing is sent.
- **One guarded entry point,** `python -m meridian_access.ai final`. It refuses unless:
  - every pin matches;
  - the model is Haiku 5.5;
  - `--final-score held_out_a1` is given;
  - the item is approved;
  - the paid window is open.

  It rehearses the whole run with a fake client and checks the worst-case cost before the first call.
- **A manual-only workflow,** `access-ai-final.yml`: dispatched by hand from `main`, behind the `access-ai` environment's approval, with a free freeze check first. It has never run.
- **The owner's answers, 2026-11-16: every recommendation in the M7 prep, except as noted.**
  - **M7-R1:** approve `aac-m7-final-score` as is, in its own pull request, after the answers below are merged. It isn't open yet. The approval takes effect only when the owner merges it.
  - **M7-R2:** the prompts are re-frozen after the M7-R16 fix ([AAC-014](014-line-breaks-are-flattened-before-any-prompt.md)). The owner confirms the new digest.
  - **M7-R3:** the run sends titles, owners and the two plan summaries. The summaries are reported as "passed n of 2".
  - **M7-R4:** k = 3, with the lowest repeat as the headline for each model-dependent number, and all three shown.
  - **M7-R5:** any false match the key confirms after the rulings, in any repeat, reopens the Haiku pick, as AAC-006 says. The held-out score is never re-run.
  - **M7-R6:** the raw results stay in the run's artifact for 30 days, marked "unruled".
  - **M7-R7:** the GitHub setup is confirmed as found on 2026-11-15, with "prevent self-review" left off on `access-ai`.
  - **M7-R8:** a run that wrote no `report.json` is not the score. The bug is fixed without opening held-out content, and the run goes again once.
  - **M7-R9:** the environment is approved at dispatch, only after the answers before the run are merged.
  - **M7-R10:** all three lists are ruled: plan findings, owners and titles.
  - **M7-R11:** "both acceptable" counts as right for the tool, listed apart with its count.
  - **M7-R12:** one ruling may cover a group with one cause, with every id still listed.
  - **M7-R13:** a helper copies and groups the rows. It never writes a ruling.
  - **M7-R14:** the four safety counts are built in code before the run. That is a code-only pull request, not built yet.
  - **M7-R15:** publish only when every id has a ruling and the report's tests pass. Then retire the world.
  - **M7-R16: fixed now** ([AAC-014](014-line-breaks-are-flattened-before-any-prompt.md)).
  - **M7-R17: measured first, then fixed.** The red-team run ([AAC-015](015-the-red-team-set.md)) measures how often a false claim built from allowed words passes the summary check. The final report calls a passing summary "passed the code check" and states the limit. A claims check follows that measurement.

**Consequences:**

- The final score can't use other prompts than the frozen ones, and the digest shows which code is running.
- Before the run: the budget pull request (M7-R1) and the safety counts (M7-R14). Then the owner's go and the environment approval.
- If Haiku makes a confirmed false match on the held-out world, the owner writes down whether to keep it, with the limit stated, or move later runs to Sonnet 5.5.
