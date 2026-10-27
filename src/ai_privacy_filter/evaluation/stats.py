"""Statistics fixed by ``METHOD.md`` section 9.

Only the two interval and summary helpers the triage stage needs are here:
Wilson score intervals for proportions and median / nearest-rank p95 for
latency. Percentiles use the nearest-rank definition in section 7.6.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final

Z_95: Final = 1.959963984540054


def wilson(successes: int, trials: int) -> dict[str, Any]:
    """Return ``k``, ``n``, the rate and its 95 % Wilson score interval.

    With ``n == 0`` the rate and bounds are ``None``: the method says a metric
    without a denominator is not estimated, never 0 % or 100 %.
    """
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("wilson needs 0 <= successes <= trials")
    if trials == 0:
        return {"k": 0, "n": 0, "rate": None, "lower": None, "upper": None}
    p = successes / trials
    z2 = Z_95 * Z_95
    centre = (p + z2 / (2 * trials)) / (1 + z2 / trials)
    half = Z_95 * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / (1 + z2 / trials)
    return {
        "k": successes,
        "n": trials,
        "rate": p,
        "lower": max(0.0, centre - half),
        "upper": min(1.0, centre + half),
    }


def median(values: Sequence[float]) -> float | None:
    """Return the median, averaging the two middle values for an even count."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def nearest_rank_p95(values: Sequence[float]) -> float | None:
    """Return the nearest-rank 95th percentile (``ceil(0.95 * n)``-th value)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return float(ordered[rank - 1])


def latency_summary(values: Sequence[int]) -> dict[str, Any]:
    """Return ``n``, median, min, max and p95 in milliseconds."""
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "p95": None}
    numbers = [float(value) for value in values]
    return {
        "n": len(numbers),
        "median": median(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "p95": nearest_rank_p95(numbers),
    }
