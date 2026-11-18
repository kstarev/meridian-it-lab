# Prompt caching for Access's Claude client

**Date:** 2026-11-18 · **Status:** Built and checked with fake clients only. No paid run, no API call. Follows lab [ADR-037](../../docs/decisions.md) and tier0's [prompt caching plan](../../docs/plans/prompt-caching.md).

## What it does

A call can mark its system prompt with `cache_control: {"type": "ephemeral"}`. The API then keeps the prompt for 5 minutes. A later call with the same prompt reads it back at a lower price. The answer does not change.

A prompt under 512 tokens is not cached. It is billed as plain input and there is no error.

## Prices (USD per million tokens)

| Model | Input | Cache write | Cache read | Output |
|---|---|---|---|---|
| Haiku 5.5 | 0.10 | 0.125 | 0.01 | 0.50 |
| Sonnet 5.5 | 2.00 | 2.50 | 0.10 | 10.00 |
| Opus 5.5 | 4.00 | 5.00 | 0.20 | 20.00 |

A write costs 1.25 times the input rate. An unknown model's cache read is priced at the highest full input rate, so it is never under-counted. With caching on, the check before a call prices the whole input as a write.

## The rule

- On by default: the model comparison on the tuning world (`python -m meridian_access.ai compare`). `--no-prompt-cache` turns it off.
- Off, and `--prompt-cache` is refused: the final held-out score and the red team on the sealed set. They were planned and frozen without the cache. They run with it only after the owner rules.
- Off: the plan summary (`plan.py`) and the library defaults in `titles.py` and `matching.py`. They are not tuning runs.

**The workflows are not changed.** The next dispatched model comparison therefore runs with the cache on. The owner should know this before dispatching it.

## Does it help here? No, not at this size

The system prompt is the only part that can be cached here. Sizes, in bytes and in estimated tokens (bytes / 1.82, the ratio the red-team harness uses, and bytes / 4 as a strict lower bound):

| Job | System prompt bytes | Tokens, bytes / 1.82 | Tokens, bytes / 4 | Minimum |
|---|---|---|---|---|
| title to role | 194 | 107 | 48 | 512 |
| account to person | 291 | 160 | 72 | 512 |
| summary | 433 | 238 | 108 | 512 |

All three are under 512 tokens, even at the generous ratio. So caching changes neither the bill nor the answer for any of the three jobs, and the cache counts will be 0. Turning it on by default is harmless and useless until a system prompt grows past 512 tokens. The system prompts are frozen, so they will not grow.

## What is built

- `ai/pricing.py`: the cache rates, `tokens_usd` for the four kinds of tokens, and a worst case with `cache_write=True`.
- `ai/spend.py`: `record` takes the two cache counts. A ledger line has them only when caching was on. Old lines still sum.
- `ai/client.py`: `ClaudeClient(..., cache_system=False)`. With it off, `request_params` is exactly what it was, so the frozen hashes and the freeze digest do not change. The client keeps `cache_write_tokens` and `cache_read_tokens` totals.
- Reports (compare, final score, red team) show the two counts only when caching was on.
- Tests: `access/tests/test_prompt_cache.py`, fake clients only.
