"""Confidence intervals for eval scores (ADR-027). Pure and seeded: no I/O.

- pass^k is a share of tickets, so it gets a Wilson 95% interval.
- pass^1 averages runs, and runs of the same ticket are not independent, so its interval
  comes from a bootstrap that resamples whole tickets.
- Two models ran on the same tickets, so their pass^k difference gets a paired bootstrap:
  each resample draws the same tickets for both.
- The realistic month (milestone 3c) makes many tickets from one template, and those tickets
  are not independent either, so its intervals resample whole templates.
"""

import math
import random
from collections.abc import Sequence

Z_95 = 1.959963984540054
BOOTSTRAP_SAMPLES = 10_000
# Fixed so a report regenerates with the same intervals.
BOOTSTRAP_SEED = 20261109


def wilson(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a proportion; (0, 1) when there is no data."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    # The bounds are exactly 0 or 1 at the edges; don't let rounding say 0.9999.
    lo = 0.0 if successes == 0 else max(0.0, centre - half)
    hi = 1.0 if successes == n else min(1.0, centre + half)
    return lo, hi


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks."""
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _resampled_means(values: Sequence[float], *, samples: int, seed: int) -> list[float]:
    rng = random.Random(seed)  # noqa: S311 - a seeded bootstrap, not a secret
    n = len(values)
    return sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples))


def ticket_bootstrap(
    runs_by_ticket: Sequence[Sequence[bool]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """95% interval for pass^1, resampling whole tickets with replacement."""
    if not runs_by_ticket:
        return 0.0, 1.0
    rates = [sum(runs) / len(runs) for runs in runs_by_ticket]
    means = _resampled_means(rates, samples=samples, seed=seed)
    return _percentile(means, 0.025), _percentile(means, 0.975)


def paired_bootstrap_difference(
    a: Sequence[bool],
    b: Sequence[bool],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> tuple[float, float, float]:
    """(observed, low, high) for mean(a) - mean(b) over the same tickets, in the same order.

    level is the interval's coverage: 0.95 for one pair, 1 - 0.05 / m for m pairs
    (Bonferroni; milestone 3 compares 6 pairs, so 99.2%).
    """
    if len(a) != len(b):
        raise ValueError("paired samples must cover the same tickets")
    if not 0 < level < 1:
        raise ValueError("level must be between 0 and 1")
    if not a:
        return 0.0, -1.0, 1.0
    diffs = [float(x) - float(y) for x, y in zip(a, b, strict=True)]
    means = _resampled_means(diffs, samples=samples, seed=seed)
    tail = (1 - level) / 2
    return sum(diffs) / len(diffs), _percentile(means, tail), _percentile(means, 1 - tail)


def template_bootstrap(
    clusters: Sequence[tuple[float, float]],
    *,
    total: bool = False,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """95% interval for a share, resampling whole templates with replacement (milestone 3c).

    Each cluster is one template: (successes, tickets), where successes may be fractional
    (passed runs / k summed over its tickets). The statistic is sum(successes) / sum(tickets)
    over the resampled templates, or with total=True the sum of successes alone (for a
    monthly total such as hours saved)."""
    if not clusters:
        return (0.0, 0.0) if total else (0.0, 1.0)
    if any(den <= 0 for _, den in clusters):
        raise ValueError("every template needs at least one ticket")
    rng = random.Random(seed)  # noqa: S311 - a seeded bootstrap, not a secret
    nums = [float(num) for num, _ in clusters]
    dens = [float(den) for _, den in clusters]
    n = len(clusters)
    values: list[float] = []
    for _ in range(samples):
        picks = [rng.randrange(n) for _ in range(n)]
        num = sum(nums[i] for i in picks)
        values.append(num if total else num / sum(dens[i] for i in picks))
    values.sort()
    return _percentile(values, 0.025), _percentile(values, 0.975)
