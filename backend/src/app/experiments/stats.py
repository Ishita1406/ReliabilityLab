"""Small, dependency-free statistics used by metrics and comparisons.

* Wilson score interval for a proportion (better than the normal approximation for small n
  and for proportions near 0 or 1).
* Newcombe's hybrid score interval for a difference of two independent proportions (method 10
  in Newcombe 1998), built from the two Wilson intervals.
* Exact McNemar test for paired binary outcomes (two-sided, exact binomial on the discordant
  pairs). Used for case-level comparisons, where the two experiments ran the same cases.
* Percentiles by linear interpolation between order statistics (the common "type 7").

All intervals are approximate 95% intervals by default and are only as valid as their
independence assumptions; see the Milestone 7 docs.
"""

import math
from collections.abc import Sequence

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("need 0 <= successes <= n and n > 0")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def newcombe_difference(k1: int, n1: int, k2: int, n2: int, z: float = Z_95) -> tuple[float, float]:
    """Interval for ``k2/n2 - k1/n1`` (candidate minus baseline)."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson_interval(k1, n1, z)
    l2, u2 = wilson_interval(k2, n2, z)
    d = p2 - p1
    lower = d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)
    upper = d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)
    return max(-1.0, lower), min(1.0, upper)


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for ``b`` and ``c`` discordant pairs (1.0 if none)."""
    if b < 0 or c < 0:
        raise ValueError("counts must be non-negative")
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / float(2**n)
    return min(1.0, 2 * tail)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("no values")
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
