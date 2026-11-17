# Plan: prompt caching for the Claude calls

**Date:** 2026-11-17 · **Status:** Built, and checked on the tuning month on 2026-11-17: 88% less cost for the two shipping arms together, with every ticket routed as it was without the cache. Off unless asked for. · **Decisions:** this file (no new ADR; the owner can turn the ones they accept into an ADR) · **Budget item:** `tier0-3c`, for the check on the tuning month only

The owner asked for prompt caching after the sealed month, as its own pull request, verified on the tuning month. The sealed month ran without it on purpose: nothing in the harness changed between the rehearsal and that run.

## What it does

Every tier0 call to Claude sends a system prompt that is the same for every ticket, then the ticket's text. With the option on, the system prompt goes out as one block marked for a 5-minute cache entry. The first call writes the entry. Every later call with the same system prompt on the same model, inside 5 minutes, reads it instead of paying for it again. A read also restarts the 5 minutes.

- **The ticket's text is never cached.** It comes after the marker and is different every time.
- **The model sees the same prompt either way,** byte for byte. Caching changes the bill and the speed, not the answer. No prompt and no prompt hash changes.
- **The picker gains the most.** Its system prompt holds every approved guide, about 2,600 tokens, and it runs on Sonnet. It was 97% of the Haiku arm's cost in the rehearsal and 66% of the Sonnet arm's.

## Prices (Anthropic's pricing page, read on 2026-11-17)

| Per million tokens | Input | Cache write, 5 minutes | Cache read | Output |
|---|---|---|---|---|
| `claude-haiku-5-5` | $0.10 | $0.125 | $0.01 | $0.50 |
| `claude-sonnet-5-5` | $2.00 | $2.50 | $0.10 | $10.00 |
| `claude-opus-5-5` | $4.00 | $5.00 | $0.20 | $20.00 |

- A write costs 1.25 times the input rate, so a prompt that is sent once and never again costs more with caching than without.
- A prompt shorter than 512 tokens is not cached on these three models. The API sends no error; it just bills the tokens as plain input.

## Decisions, and why

1. **Off unless asked for.** `ClaudeModel(cache_system=True)` turns it on. With it off, every request and every ledger line is what it was before this change.
   - Why not on everywhere: several eval reports work their cost out from input and output tokens only (the 3a, 3b, 3d and milestone 4 reports). With caching on they would under-report, while the ledger stayed right. They aren't changed here, so they keep running without the cache.
2. **Where it can be turned on:**
   - the month runner: `python -m tier0.evals.month run ... --prompt-cache`;
   - the agent's own model: `TIER0_CLAUDE_PROMPT_CACHE=1`, next to `TIER0_CLAUDE_MODEL`. It is off by default there too.
3. **One marker, on the system prompt.** Not the request-level automatic marker: that one lands after the ticket's text, so every call would pay a write for bytes nobody reads again.
4. **The 5-minute entry, not the 1-hour one.** A month run sends the same prompt every few seconds, so the entry stays warm, and the 1-hour entry costs 2 times the input rate to write.
5. **The spend cap still holds.** Before each call the ledger checks the worst case, which now prices the whole input as a cache write (1.25 times) when the option is on. After the call, the ledger prices plain input, cache writes and cache reads each at their own rate.
   - A model id the price table doesn't know is priced at the highest known rates, and its cache reads at the full input rate, so an unknown model is over-counted, never under-counted.
6. **A sealed set never runs with it.** The sealed month's one run was made without the cache, and the runner refuses `--prompt-cache` on a sealed set.
7. **A cached run gets its own result name,** ending in `-cache`, so it can't be mistaken for, or resume, an uncached run.

## What is built

- `tier0/models/pricing.py`: the cache rates, and one function that prices a call's four kinds of tokens.
- `tier0/models/spend.py`: the ledger records cache writes and reads, at their rates. A line without caching has the same fields as before.
- `tier0/models/claude.py`: the option, the marker, and the cache tokens read from the response.
- `tier0/models/__init__.py`: `TIER0_CLAUDE_PROMPT_CACHE=1` for the agent's model.
- `tier0/evals/month.py`: `--prompt-cache`, the cache tokens per task in the result, the cost from all four kinds of tokens, and one more line in the summary. A result without the option is written and rendered as before.

## Verified on the tuning month (2026-11-17)

**These are tuning numbers, and they are about cost.** The prompts were shaped on the tuning month, so its scores say little about quality. This check shows the bill, and that the routes don't change.

Both shipping arms ran the tuning month once with `--prompt-cache`: 300 tickets, seed 1130, k = 1, the Sonnet picker, from commit `e10cb9f`. They ran after the sealed month had finished. Each is compared with the rehearsal of the same day, which ran the same month, prompts, picker and k without the cache. The generated summaries: [haiku-3d2](../evals/2026-11-17-prompt-cache-tuning-month-haiku-3d2.md) and [sonnet-3d2](../evals/2026-11-17-prompt-cache-tuning-month-sonnet-3d2.md).

| | `haiku-3d2` | `sonnet-3d2` | Both |
|---|---|---|---|
| Cost without the cache (the rehearsal) | $1.1681 | $1.7147 | $2.8828 |
| Cost with the cache | $0.1307 | $0.2149 | $0.3456 |
| Saved | $1.0374, 89% | $1.4998, 87% | $2.5372, 88% |
| Tickets routed the same way as without the cache | 300 of 300 | 300 of 300 | |
| Out-of-scope tickets wrongly answered | 0 of 208, as before | 0 of 208, as before | |
| Failed calls | 0 | 0 | |

Where the input tokens went with the cache on:

| Arm, task and model | Calls | At the full rate | Written to the cache | Read from it | Share read |
|---|---|---|---|---|---|
| `haiku-3d2`, picker, Sonnet | 202 | 11,539 | 2,652 | 533,052 | 97% |
| `haiku-3d2`, triage, Haiku | 300 | 12,756 | 878 | 262,522 | 95% |
| `haiku-3d2`, own-account check, Haiku | 40 | 3,036 | 669 | 26,091 | 88% |
| `sonnet-3d2`, picker, Sonnet | 201 | 11,487 | 0 | 533,052 | 98% |
| `sonnet-3d2`, triage, Sonnet | 300 | 12,756 | 788 | 235,612 | 95% |
| `sonnet-3d2`, own-account check, Sonnet | 40 | 3,036 | 540 | 21,060 | 85% |

- **Each system prompt was written once, and every later call read it.** No entry expired during a run: a run sends a call every second or two.
- **The Sonnet arm's picker wrote nothing.** Both arms use the same picker prompt on the same model, and the Sonnet arm started seconds after the Haiku arm ended. So it read the entry the Haiku arm had written.
- **The models got the same prompts.** For every task, the three kinds of input tokens add up to exactly the rehearsal's input tokens: 547,243 for the Haiku arm's picker, for example. Output tokens are the same for triage and the check, and differ by 1 and 3 tokens for the picker.
- **Every ticket took the same route:** the same outcome, guide and stage, 300 of 300 in both arms. The models' answers aren't fixed from run to run, so a ticket or two could have differed without the cache being the cause. None did.
- **The ledger agrees with the summaries.** `tier0-3c` rose by $0.3456 during the check, and the two summaries' costs add up to $0.3456.
- **The check cost $0.35.** The limit set for it was $1.00, with a spend gate before each arm and a watchdog on the ledger. `tier0-3c` is now at $16.63 of its $18 cap.
- **For scale:** the sealed month cost $9.04 at k = 3 without the cache. At these savings a run like it would cost about $1.10. That is an estimate from tuning data. The sealed month is spent and is not run again.

What the check doesn't show:

- **The live agent's traffic.** A 5-minute entry expires between tickets that come further apart, and then the next call pays a write.
- **k above 1.** Later runs would read the entries the first run wrote, so the saving should not be smaller. It wasn't measured.
- **Anything about quality on unseen wording.** The cache doesn't change what the model is sent, and the tuning month can't show more than that.

## Left out, and why

- **The other eval reports' cost lines** (decision 1). They keep running uncached until each is taught the cache tokens.
- **The sealed month's cost estimate** (`sealed-cost`) still prices uncached calls. The month is spent, so it has no run left to price.
- **A 1-hour entry or a keep-alive for the live agent.** A real help desk sees a ticket every few minutes at best, so a 5-minute entry would often have expired. Whether caching pays there depends on traffic the lab doesn't have. This change only makes the option available.
