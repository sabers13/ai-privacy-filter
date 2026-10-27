"""Statistics for the evaluation report.

The numbers follow ``METHOD.md`` section 9: 95 % Wilson score intervals for
every proportion and a percentile cluster bootstrap as a sensitivity check.

The bootstrap is implemented here with the standard library so the result does
not depend on numpy's random generator, numpy's version or numpy's rounding.
``random.Random`` (Mersenne Twister) is stable across Python releases and the
percentile uses the linear interpolation that ``numpy.percentile(...,
method="linear")`` uses.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Final

Z_95: Final = 1.959963984540054
BOOTSTRAP_SEED: Final = 20261027
BOOTSTRAP_RESAMPLES: Final = 10_000


def wilson(successes: int, trials: int) -> tuple[float, float]:
    """Return the 95 % Wilson score interval for ``successes / trials``.

    Returns the raw proportions (0..1). A zero denominator is a programming
    error here; callers write ``not estimable`` instead of calling this.
    """

    if trials <= 0:
        raise ValueError("wilson needs a positive number of trials")
    if successes < 0 or successes > trials:
        raise ValueError("wilson successes must be between 0 and trials")
    p = successes / trials
    z2 = Z_95 * Z_95
    denominator = 1.0 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denominator
    half = Z_95 * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Linear-interpolation percentile over an already sorted sequence."""

    if not sorted_values:
        raise ValueError("percentile needs at least one value")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap_ratio(
    pairs: Sequence[tuple[int, int]],
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[float, float, int]:
    """Cluster bootstrap the ratio ``sum(numerator) / sum(denominator)``.

    ``pairs`` is one ``(numerator, denominator)`` per cluster (an enquiry for
    synthetic data, a sentence for GermEval). Whole clusters are resampled with
    replacement. A resample whose denominator is zero is skipped and counted.
    Returns ``(low, high, skipped)`` as raw proportions.
    """

    if not pairs:
        raise ValueError("bootstrap needs at least one cluster")
    rng = random.Random(seed)
    size = len(pairs)
    numerators = [pair[0] for pair in pairs]
    denominators = [pair[1] for pair in pairs]
    ratios: list[float] = []
    skipped = 0
    for _ in range(resamples):
        numerator = 0
        denominator = 0
        for _ in range(size):
            index = rng.randrange(size)
            numerator += numerators[index]
            denominator += denominators[index]
        if denominator == 0:
            skipped += 1
            continue
        ratios.append(numerator / denominator)
    if not ratios:
        raise ValueError("every bootstrap resample had a zero denominator")
    ratios.sort()
    return percentile(ratios, 0.025), percentile(ratios, 0.975), skipped


def percent(fraction: float) -> float:
    """Return a fraction as a percentage rounded to one decimal place."""

    return round(fraction * 100.0, 1)


def percent_interval(low: float, high: float) -> list[float]:
    """Return a raw interval as a one-decimal percentage pair."""

    return [percent(low), percent(high)]


def format_percent(value: float | None) -> str:
    """Render a one-decimal percentage cell, or ``n/a`` for a missing value."""

    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def format_interval(interval: Sequence[float] | None) -> str:
    """Render a percentage interval as ``low-high`` (en dash, one decimal)."""

    if interval is None or len(interval) != 2:
        return "n/a"
    return f"{interval[0]:.1f}-{interval[1]:.1f}"
