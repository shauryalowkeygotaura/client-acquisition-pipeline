"""
modules/significance.py — statistical guard for the self-improving loop.

The learning loop (modules/learning.py) compares conversion rates between
options — message angle A vs B, channel X vs Y. With small samples those gaps
are mostly noise: 2/3 "beats" 5/10 only by luck. Acting on noise makes the
system thrash and chase ghosts.

This is the gate: a two-proportion z-test plus a hard minimum-sample floor.
learning.py only commits a change when the difference clears BOTH. Slim on
purpose — no scipy, just the closed-form normal-approximation z-test, which is
plenty for the few-hundred-lead scale this pipeline runs at.
"""
from __future__ import annotations

import math

# Two-tailed critical z by alpha. 1.96 ≈ 95% confidence (the default).
_Z_CRIT = {0.10: 1.645, 0.05: 1.960, 0.01: 2.576}


def two_proportion_significant(
    s1: int, n1: int, s2: int, n2: int, *, min_n: int = 20, alpha: float = 0.05
) -> bool:
    """
    True if proportion s1/n1 differs significantly from s2/n2.

    s = successes (e.g. replies), n = trials (e.g. sends). Returns False when
    either arm has fewer than min_n trials (not enough data to trust), or the
    pooled standard error is zero (degenerate), or the gap fails the z-test.
    """
    if n1 < min_n or n2 < min_n:
        return False
    if s1 < 0 or s2 < 0 or s1 > n1 or s2 > n2:
        return False

    p1, p2 = s1 / n1, s2 / n2
    pooled = (s1 + s2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return False

    z = abs(p1 - p2) / se
    return z >= _Z_CRIT.get(alpha, 1.960)
