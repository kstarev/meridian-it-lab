"""Where the prompt cache is on by default for an eval run (docs/plans/prompt-caching.md).

On for a Claude model on dev and tuning data; off for a sealed run until the owner rules.
Every eval's command line takes `--prompt-cache` / `--no-prompt-cache` from here, and
resolves the flag with `resolve`, so the default is decided in one place.
"""

import argparse
from collections.abc import Mapping, Sequence
from typing import Any

CLAUDE = "claude"


def default_prompt_cache(*, provider: str, sealed: bool) -> bool:
    """True only for the Claude API on a run that is not sealed."""
    return provider == CLAUDE and not sealed


def add_flags(parser: argparse.ArgumentParser) -> None:
    """`--prompt-cache` and `--no-prompt-cache`; None when neither is given."""
    parser.add_argument(
        "--prompt-cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Claude models only: cache each call's system prompt for 5 minutes. On by default "
        "for dev and tuning runs, off for a sealed run (--no-prompt-cache turns it off)",
    )


def resolve(flag: bool | None, *, provider: str, sealed: bool) -> tuple[bool, str | None]:
    """(use the cache, a reason to refuse or None). No flag gives the default; `False` is
    always allowed; `True` is refused on a sealed run and for a model that isn't Claude."""
    if flag is None:
        return default_prompt_cache(provider=provider, sealed=sealed), None
    if not flag:
        return False, None
    if sealed:
        return False, (
            "this sealed run was planned without the prompt cache; it runs with it only after "
            "the owner rules"
        )
    if provider != CLAUDE:
        return False, f"--prompt-cache is for Claude models; this run uses {provider}"
    return True, None


def result_note(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """One line, and a blank one, for a report built from saved scores when some of them used
    the cache; nothing otherwise, so a report of uncached scores is unchanged."""
    cached = [r for r in results if r.get("prompt_cache")]
    if not cached:
        return []
    plain = sum(r["input_tokens"] for r in cached)
    written = sum(r.get("cache_write_tokens", 0) for r in cached)
    read = sum(r.get("cache_read_tokens", 0) for r in cached)
    total = plain + written + read
    share = read / total if total else 0.0
    return [
        f"Prompt cache on for {len(cached)} of {len(results)} scores: each call's system prompt "
        f"is cached for 5 minutes. Input tokens in those scores: {plain:,} at the full rate, "
        f"{written:,} written to the cache and {read:,} read from it ({share:.0%} of the input "
        "was read from the cache). Their costs price each kind at its own rate.",
        "",
    ]
