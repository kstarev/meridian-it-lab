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
FREE_PROVIDERS = frozenset({"ollama", "stub", "test"})


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for one call; 0 for local models. Unknown Claude models raise, so a price
    can't silently be missing from a report."""
    if provider in FREE_PROVIDERS or provider.startswith("stub"):
        return 0.0
    if model not in PRICES_PER_MTOK:
        raise KeyError(f"no price for {model}; add it to tier0/models/pricing.py")
    per_in, per_out = PRICES_PER_MTOK[model]
    return input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000
