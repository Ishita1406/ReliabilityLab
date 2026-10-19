"""Statistics helpers against published reference values."""

import pytest

from app.experiments.stats import (
    mcnemar_exact_p,
    newcombe_difference,
    percentile,
    wilson_interval,
)


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (0, 10, 0.0, 0.2775),  # never 0..0: zero successes still leave real uncertainty
        (10, 10, 0.7225, 1.0),
        (5, 10, 0.2366, 0.7634),
        (81, 263, 0.2553, 0.3662),  # Newcombe (1998) / Wilson example
    ],
)
def test_wilson_interval(k: int, n: int, low: float, high: float) -> None:
    assert wilson_interval(k, n) == pytest.approx((low, high), abs=5e-4)


def test_wilson_rejects_empty_samples() -> None:
    with pytest.raises(ValueError):
        wilson_interval(0, 0)


def test_newcombe_difference_matches_the_published_example() -> None:
    # Newcombe (1998), example (a): 56/70 vs 48/80, difference 0.2000, 95% CI 0.0524 to 0.3339.
    assert newcombe_difference(48, 80, 56, 70) == pytest.approx((0.0524, 0.3339), abs=5e-4)


@pytest.mark.parametrize(
    ("b", "c", "p"),
    [
        (0, 0, 1.0),
        (0, 1, 1.0),
        (0, 2, 0.5),
        (0, 5, 0.0625),  # five-for-five discordant pairs are still not significant at 0.05
        (0, 6, 0.03125),
        (3, 3, 1.0),
        (2, 9, 0.06543),
    ],
)
def test_exact_mcnemar(b: int, c: int, p: float) -> None:
    assert mcnemar_exact_p(b, c) == pytest.approx(p, abs=5e-5)
    assert mcnemar_exact_p(c, b) == pytest.approx(p, abs=5e-5)


def test_percentile_interpolates_linearly() -> None:
    values = [4.0, 1.0, 3.0, 2.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 2.5
    assert percentile(values, 95) == pytest.approx(3.85)
    assert percentile(values, 100) == 4.0
    assert percentile([7.0], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)
