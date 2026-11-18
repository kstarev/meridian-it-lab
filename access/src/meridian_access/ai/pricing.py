"""API prices in USD per million tokens (the design of tier0's table, copied)."""

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
ALIASES = {"haiku": HAIKU, "sonnet": SONNET, "opus": OPUS}
# The model Access-as-Code uses, picked by the owner on 2026-11-15 after the comparison at k=3
# (AAC-006): the three models tied within the 95% intervals, so the cheapest was chosen.
CHOSEN_MODEL = HAIKU

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    HAIKU: (0.10, 0.50),
    SONNET: (2.00, 10.00),
    OPUS: (4.00, 20.00),
}

# Prompt caching with 5-minute entries (the design of tier0's table, copied): a cache write costs
# 1.25 times the model's input rate, and a cache read has its own rate.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_PER_MTOK: dict[str, float] = {
    HAIKU: 0.01,
    SONNET: 0.10,
    OPUS: 0.20,
}


def model_id(name: str) -> str:
    """`haiku`, `sonnet` or `opus` (or a full id) to a full model id; ValueError if unknown."""
    full = ALIASES.get(name.strip().lower(), name.strip())
    if full not in PRICES_PER_MTOK:
        raise ValueError(f"unknown model {name!r}; known: {', '.join(sorted(ALIASES))}")
    return full


def highest_rate() -> tuple[float, float]:
    return (
        max(rate[0] for rate in PRICES_PER_MTOK.values()),
        max(rate[1] for rate in PRICES_PER_MTOK.values()),
    )


def rate_for(model: str) -> tuple[tuple[float, float], bool]:
    """The model's rates, and whether they are its own (False: the highest known rate)."""
    if model in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[model], True
    return highest_rate(), False


def tokens_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """USD for one call's four kinds of tokens. `input_tokens` is the input billed at the full
    rate. An unknown model's cache read is priced at the highest full input rate, so it is
    never under-counted."""
    (per_in, per_out), known = rate_for(model)
    per_read = CACHE_READ_PER_MTOK[model] if known else per_in
    usd = input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000
    if cache_write_tokens:
        usd += cache_write_tokens * per_in * CACHE_WRITE_MULTIPLIER / 1_000_000
    if cache_read_tokens:
        usd += cache_read_tokens * per_read / 1_000_000
    return usd


def worst_case_usd(
    model: str, input_tokens: int, output_tokens: int, *, cache_write: bool = False
) -> float:
    """An upper bound on one call. With `cache_write` the whole input is priced as a cache
    write (the call may write one)."""
    (per_in, per_out), _ = rate_for(model)
    if cache_write:
        per_in *= CACHE_WRITE_MULTIPLIER
    return input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000
