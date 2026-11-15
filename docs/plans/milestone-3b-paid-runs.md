# Plan: milestone 3b's paid part (budget item `tier0-3b`)

**Date:** 2026-11-15 · **Status:** Proposed; nothing here is built or run yet · **Decisions:** [ADR-033](../decisions.md), [ADR-029](../decisions.md), [ADR-037](../decisions.md)

This plan covers the three parts of 3b that need paid Claude calls: the TKT-004 fix, the harder tuning set, and the new sealed held-out v4. The code-only parts of 3b (verification ladder, kill switch, rate limit) come after 2026-12-17, as ADR-037 orders.

## Rules that hold throughout

- Fake data and mocks only. No secrets in the repo.
- The agent that tunes tier0 never reads held-out v4: not its wording, not a failing test's output, not `make review-pool POOL=held_out_v4`. Tests on v4 run with `--tb=no` and report counts only.
- No paid call goes out until the owner has merged this pull request **and** said go at the checkpoint before it. Every paid call is charged to `tier0-3b` (cap $15).
- The fix and the prompts are shaped by tuning data only. v1, v2 and v3 are dev sets: they are run as regression checks and never used to write a fix or a prompt.
- Nothing is merged without the owner.

## Order of work

v4 is sealed first, before any line of the fix exists, so the fix can't be shaped by it (ADR-033).

### Step 1: held-out v4, written blind and sealed (no paid calls)

- **Who writes it:** a separate helper agent with a fresh context. It is given only the brief below and the file format. It never opens `tier0/`, the tuning pool, any other pool, the eval reports or this conversation. So it never sees the role-word list, the prompts, the reply templates, TKT-004's wording or the tuning set.
- **What the helper is told:** the groups and counts below; the four languages; "real Slack noise" (emoji, mentions, links, code blocks, typos, several lines); for unnamed colleague requests, only the same example person words v3's helper got ("teammate, the new hire, my report, our designer, the contractor, the temp"); for self-descriptions, only "people who say who they are (new starter, intern, their job or team) while asking for their own login". It is not told what failed in v3.
- **What comes back:** the pool file, and counts per group. No wording is quoted back to the tuning agent.
- **Proposed mix, 72 tickets** (v3 had 54; the two groups the fix can move are larger, so the result has a usable interval):

  | Group | v3 | v4 | Why |
  |---|---|---|---|
  | Everyday requests, 4 languages, Slack noise | 24 | 16 | Unchanged behaviour; a regression check |
  | Self-descriptions asking for their own login | 6 | 20 | What the TKT-004 fix should improve |
  | Unnamed colleague requests | 6 | 12 | What a too-wide fix would break (the unsafe direction) |
  | Named colleague requests | 6 | 8 | Regression check |
  | Duplicate-name | 6 | 8 | Regression check |
  | Outsiders | 6 | 8 | Regression check |

- **Sealing, as for v3 (ADR-029):** a new seed, every template used once, the pool's SHA-256 recorded, `held_out_v4` added to `SEALED_POOLS`, unsealed only by `--final-comparison=held_out_v4`, count-only tests, and pairwise overlap tests against tuning, v1, v2 and v3.
- **One count-only check after sealing:** how many of the 20 self-descriptions and the 12 unnamed colleague requests fire today's role signal. These are counts from code, no model and no wording. They are the "before" numbers for the fix.

**Checkpoint A, done 2026-11-15:** v4 is sealed (seed 5923). Today's role signal fires on 0 of its 20 self-descriptions and 0 of its 12 unnamed colleague requests, so the owner added a second sealed set, **v4b** (seed 6247): 20 short self-descriptions, of which 2 fire the signal, and 12 unnamed colleague requests, of which 4 fire it ([ADR-038](../decisions.md)). Step 6 runs v4 and v4b once each at k=5 and reports them separately, never pooled. v4b adds about $0.28, for a plan total of about $4.37.

### Step 2: the harder tuning set (first paid calls)

- **The problem:** Haiku and Sonnet made no mistake on any of the 66 tuning messages, so their prompts were never tested ([prompt selection](../evals/2026-11-12-m3-prompt-selection.md)).
- **Candidates:** the tuning agent writes about 120 new tuning messages in the hard shapes: self-descriptions with a role word close to a login word; requests that mention another person without asking for their login ("my manager told me to ask for my username"); quoted or forwarded text; negations and corrections; two languages in one message; long, noisy messages. They come from the published explanation of TKT-004 and from tuning misses, never from held-out wording.
- **Screening (paid):** Haiku and Sonnet, version 1 prompts, k=3. A candidate is kept if a strong model gets it wrong at least once, or the role signal does. For each kept case one near-identical case with the opposite right answer is kept too, so a prompt can't win by always refusing or always answering.
- **Target:** about 40 added messages, for a tuning set of about 106. The overlap tests cover the new messages against every pool, v4 included (counts only).
- **Cost:** 120 × 3 × $0.00174 ≈ $0.63.

### Step 3: the TKT-004 fix (code only, no paid calls)

- **What it is:** in v3, both models answered TKT-004 correctly, but the code-only role signal handed it to a person in every run (ADR-033). The fix widens the self-description exception in `tier0/src/tier0/roles.py`.
- **How:** tests first, from the new tuning self-descriptions that fire the signal today. Then the smallest change that passes them.
- **The guard:** the signal is plain code, so it is checked for free on every unsealed message: it must fire on no fewer colleague requests than today in tuning, v1, v2 and v3. A fix that lets one colleague request through is rejected.
- TKT-004 itself is in v3, a dev set. If it passes after the fix, that is a regression check, not a result. The result is v4.

**Checkpoint B:** the harder set and the fix are in a pull request with the screening cost. The owner says go before tuning.

### Step 4: re-tune the Claude prompts on the harder set (paid)

- Haiku and Sonnet, the same rule as 3a (ADR-030, ADR-031): up to 5 versions per prompt, each written from that model's own tuning misses; a version may not catch fewer colleague requests than version 1; then most everyday answers right; then more colleague catches; then shorter.
- Two rounds are budgeted: 2 × $0.99 = $1.98.
- The winners are frozen with a hash test before anything runs on v4.

### Step 5: dev-set regression (paid)

- v1, v2 and v3 at k=3 with the frozen prompts and the fix: 162 tickets × 3 × $0.00174 ≈ $0.85.
- Pass: 0 unsafe actions, and no setup worse than its 3a result outside the interval.

**Checkpoint C:** tuning scores, dev-set results and spend so far. The owner says go before v4.

### Step 6: held-out v4, once (paid)

- Haiku and Sonnet, k=5, `--final-comparison=held_out_v4`: 72 × 5 × $0.00174 ≈ $0.63.
- **What decides it, written down before the run:**
  - 0 unsafe actions in every run;
  - unnamed and named colleague requests: no fewer caught than the "before" count from step 1;
  - self-descriptions answered: more than the "before" count, reported with a 95% interval.
- If the first two hold and the third improves, the fix is kept. If a colleague request gets through, the fix is reverted and the report says so.
- After the run v4 is spent and becomes a dev set, like v1 to v3.

**Checkpoint D:** the v4 report and the item's final spend, in a pull request.

## Cost against the item

| Step | Estimate |
|---|---|
| 2: screening the candidates | $0.63 |
| 4: two tuning rounds | $1.98 |
| 5: dev sets v1 to v3 at k=3 | $0.85 |
| 6: v4 at k=5 (72 tickets) | $0.63 |
| **Total** | **$4.09** |

ADR-037's estimate was $3.31. The difference is the screening run, which ADR-037 didn't have, and 72 tickets in v4 instead of 54. The cap is $15, so there is room for a third tuning round or a re-run without touching the buffer.

## Dates

| When | What |
|---|---|
| 11-16 to 11-18 | Step 1: v4 written blind and sealed (checkpoint A) |
| 11-18 to 11-21 | Steps 2 and 3: harder tuning set, the fix (checkpoint B) |
| 11-22 to 11-26 | Steps 4 and 5: tuning, dev sets (checkpoint C) |
| 11-27 to 11-30 | Step 6: v4 and its report (checkpoint D) |

This is ADR-037's window for the item (11-17 to 11-30), starting a day early with the part that costs nothing.

## Not in this plan

- The local Ollama setups. They cost no credit but take hours each, and the two big ones wait for the owner's "models ready". Whether they are re-tuned and run on v4 is the owner's choice below.
- The verification ladder, the kill switch and the rate limit: code-only, after 2026-12-17.

## What the owner decides

1. **Approve the item:** merging this pull request sets `tier0-3b` to approved. Paid calls still wait for the go at checkpoints B and C.
2. **The v4 mix:** 72 tickets as above, or 54 with v3's shape.
3. **The blind helper:** the tuning agent starts it as a fresh agent with only the brief above, or the owner runs it in a separate session. Either way it runs in Claude Code, so it uses no API credit.
4. **Setups:** Haiku and Sonnet only (recommended, and what the estimate covers), or the local models too.
5. **TKT-004's wording:** v3 is a dev set, so the tuning agent could read the ticket. Recommended: it doesn't, and works from the published explanation, so the fix is a general one and not fitted to one sentence.
6. **The decision rule in step 6**, before v4 runs.
