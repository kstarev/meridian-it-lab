"""API prices for cost per run and cost per ticket (milestone 3).

Anthropic first-party rates in USD per million tokens, for prompts up to 100K tokens
(every tier0 prompt is far below that). Ollama runs on this machine: 0 API cost, and the
hardware and electricity aren't counted.
"""

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
# The only provider that bills per token. Everything else (Ollama, the stub, test fakes)
# runs on this machine.
PAID_PROVIDER = "anthropic"


def highest_rate() -> tuple[float, float]:
    """The highest known input and output rates, for pricing what we can't name."""
    return (
        max(rate[0] for rate in PRICES_PER_MTOK.values()),
        max(rate[1] for rate in PRICES_PER_MTOK.values()),
    )


def rate_for(model: str) -> tuple[tuple[float, float], bool]:
    """The model's rates, and whether they're its own (False: the highest known rate)."""
    if model in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[model], True
    return highest_rate(), False


def worst_case_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """An upper bound on one call's cost, before it is sent (the spend cap, ADR-030)."""
    (per_in, per_out), _ = rate_for(model)
    return input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for one call; 0 for anything but the Claude API. An unknown Claude model
    raises, so a price can't silently be missing from a report."""
    if provider != PAID_PROVIDER:
        return 0.0
    if model not in PRICES_PER_MTOK:
        raise KeyError(f"no price for {model}; add it to tier0/models/pricing.py")
    per_in, per_out = PRICES_PER_MTOK[model]
    return input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000
