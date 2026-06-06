"""
modules/significance.py — v3 A/B significance testing

Two-proportion z-test for variant comparisons. Pure stdlib math, zero deps.

The optimizer consumes this before recommending a variant. Without it, the
optimizer chases week-to-week variance and overshoots every cycle. With it,
recommendations are gated on statistical evidence that the difference is real.

Convention:
  - p_value < 0.05 → significant at the 95% confidence level
  - sample size warning: minimum 30 per group before any test is reliable
"""
import math


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via erf. Stdlib-only."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_proportion_z(
    a_successes: int, a_trials: int,
    b_successes: int, b_trials: int,
) -> tuple[float, float]:
    """
    Two-tailed z-test. Returns (z_statistic, p_value).

    Use case: did variant B reply at a different rate than variant A?
    Null hypothesis: the two true rates are equal.
    """
    if a_trials == 0 or b_trials == 0:
        return 0.0, 1.0

    p_a = a_successes / a_trials
    p_b = b_successes / b_trials
    pooled = (a_successes + b_successes) / (a_trials + b_trials)

    se = math.sqrt(pooled * (1 - pooled) * (1 / a_trials + 1 / b_trials))
    if se == 0:
        return 0.0, 1.0

    z = (p_b - p_a) / se
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return z, p_value


def min_sample_for_effect(
    baseline_rate: float,
    expected_lift: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """
    Approximate per-group sample needed to detect `expected_lift` from
    `baseline_rate` at the given alpha + power. Uses the standard
    two-proportion power formula.

    Example: baseline 5% reply, expected lift to 7% (40% relative lift)
             at alpha=0.05, power=0.80 → ~1200 per group.

    This is the reality check the optimizer needs: with 30 leads per variant
    per week, a 2pp lift takes ~8 weeks to detect.
    """
    # z values for two-sided alpha and one-sided power
    z_alpha = 1.96 if abs(alpha - 0.05) < 0.001 else 1.645
    z_beta = 0.84 if abs(power - 0.80) < 0.001 else 1.28

    p1 = baseline_rate
    p2 = baseline_rate + expected_lift
    if p2 <= 0 or p2 >= 1:
        return -1  # invalid effect size

    p_bar = (p1 + p2) / 2
    numerator = (z_alpha * math.sqrt(2 * p_bar * (1 - p_bar)) +
                 z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    denominator = (p2 - p1) ** 2
    if denominator == 0:
        return -1
    return int(math.ceil(numerator / denominator))


def significance_test(
    a_sent: int, a_replies: int,
    b_sent: int, b_replies: int,
    alpha: float = 0.05,
    min_per_group: int = 30,
) -> dict:
    """
    Run a two-proportion z-test for reply-rate comparison.

    Returns dict with keys:
      z, p_value, significant, winner, reply_rate_a, reply_rate_b,
      lift_absolute, lift_relative, sample_warning, min_sample_needed.

    `winner` is "A", "B", or "tie" — only populated when significant=True.
    """
    z, p = two_proportion_z(a_replies, a_sent, b_replies, b_sent)
    rate_a = (a_replies / a_sent) if a_sent else 0.0
    rate_b = (b_replies / b_sent) if b_sent else 0.0
    lift_abs = rate_b - rate_a
    lift_rel = (lift_abs / rate_a) if rate_a > 0 else 0.0

    significant = p < alpha and a_sent >= min_per_group and b_sent >= min_per_group
    sample_warning = (a_sent < min_per_group or b_sent < min_per_group)

    if significant:
        winner = "B" if rate_b > rate_a else "A"
    else:
        winner = "tie"

    # If sample is too small, suggest how much more is needed for the
    # observed effect to become detectable.
    min_sample = None
    if sample_warning and rate_a > 0 and abs(lift_abs) > 0:
        min_sample = min_sample_for_effect(rate_a, abs(lift_abs), alpha=alpha)

    return {
        "z": round(z, 3),
        "p_value": round(p, 4),
        "significant": significant,
        "winner": winner,
        "reply_rate_a": round(rate_a, 4),
        "reply_rate_b": round(rate_b, 4),
        "lift_absolute": round(lift_abs, 4),
        "lift_relative": round(lift_rel, 4),
        "sample_warning": sample_warning,
        "min_sample_needed_per_group": min_sample,
    }
