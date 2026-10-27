"""Unit tests for the evaluation statistics."""

from __future__ import annotations

import pytest

from eval.stats import (
    BOOTSTRAP_SEED,
    bootstrap_ratio,
    format_interval,
    percent,
    percent_interval,
    percentile,
    wilson,
)


@pytest.mark.parametrize(
    ("successes", "trials", "expected"),
    [
        (0, 10, (0.0, 0.27753279986288965)),
        (10, 10, (0.7224672001371103, 1.0)),
        (5, 10, (0.23659309081712285, 0.7634069091828772)),
        (0, 160, (0.0, 0.02344619517150519)),
    ],
)
def test_wilson_matches_the_documented_score_interval(
    successes: int, trials: int, expected: tuple[float, float]
) -> None:
    low, high = wilson(successes, trials)
    assert low == pytest.approx(expected[0])
    assert high == pytest.approx(expected[1])


def test_wilson_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        wilson(0, 0)
    with pytest.raises(ValueError):
        wilson(2, 1)


def test_percentile_uses_linear_interpolation() -> None:
    values = [0.0, 10.0]
    assert percentile(values, 0.25) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0], 0.5) == pytest.approx(2.0)


def test_bootstrap_is_seeded_and_reports_skipped_resamples() -> None:
    pairs = [(2, 10), (0, 0), (1, 5)]
    first = bootstrap_ratio(pairs, resamples=200)
    second = bootstrap_ratio(pairs, resamples=200)
    assert first == second
    low, high, skipped = first
    assert 0.0 <= low <= high <= 1.0
    assert 0 <= skipped <= 200
    assert BOOTSTRAP_SEED == 20261027


def test_percentage_helpers_round_to_one_decimal() -> None:
    assert percent(0.12345) == 12.3
    assert percent_interval(0.0, 1.0) == [0.0, 100.0]
    assert format_interval([0.0, 100.0]) == "0.0-100.0"
    assert format_interval(None) == "n/a"
