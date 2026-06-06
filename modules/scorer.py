"""
modules/scorer.py — Phase 2 + v3 upgrade

Dual-axis lead scoring:
  pain_score (0–10):     existing urgency × call_volume × revenue × niche_bonus
  adoption_score (0–10): NEW — digital maturity × budget proxy × language fit

Combined lead_score is a weighted average. Defaults to 60/40 pain/adoption because
pain still drives urgency, but adoption stops the scorer from upranking
high-pain leads that will never actually buy (paper-booking 58-year-old etc.).

Both component scores are persisted to the Sheet so the optimizer can analyze
them separately without re-deriving anything.

No API calls. Weights are tunable in WEIGHTS dict below.
"""
import os

WEIGHTS: dict = {
    "hiring_urgency": {"high": 4, "medium": 2, "low": 0},
    "likely_call_volume": {"high": 3, "medium": 2, "low": 0},
    "revenue_dependency_on_calls": {"high": 2, "medium": 1, "low": 0},
    "niche_bonus": {
        "dental": 1, "medical": 1, "legal": 1,
        "physio": 1, "optometry": 1, "veterinary": 1,
        "salon": 0, "trades": 0, "hotel": 0,
        "school": -1, "general": -1,
    },
    # v3 adoption weights — additive within adoption_score (capped at 10)
    "budget_proxy": {"high": 3, "medium": 2, "low": 0, "unknown": 0},
    "language_match_bonus": {"hinglish": 1, "english_default": 0},
    # Composite weighting between pain and adoption.
    # Override via env: PAIN_WEIGHT=0.5 etc. Must sum to 1.0.
    "composite": {
        "pain": float(os.getenv("SCORER_PAIN_WEIGHT", "0.6")),
        "adoption": float(os.getenv("SCORER_ADOPTION_WEIGHT", "0.4")),
    },
}


def _pain_score(data: dict) -> int:
    """Original 0–10 score: how badly do they hurt right now."""
    score = 0
    score += WEIGHTS["hiring_urgency"].get(data.get("hiring_urgency", "medium"), 2)
    score += WEIGHTS["likely_call_volume"].get(data.get("likely_call_volume", "medium"), 2)
    score += WEIGHTS["revenue_dependency_on_calls"].get(
        data.get("revenue_dependency_on_calls", "medium"), 1
    )
    score += WEIGHTS["niche_bonus"].get(data.get("niche", "general"), 0)
    return max(0, min(10, score))


def _adoption_score(data: dict) -> int:
    """0–10 score: how likely are they to actually buy and adopt.

    Digital maturity (6 weight points) + budget proxy (3) + language fit (1).
    A high-pain lead with 0 adoption signals scores 0 here — they need traditional
    sales, not automated outbound.
    """
    score = data.get("digital_maturity_score", 0) * 0.6  # 0–6 contribution
    score += WEIGHTS["budget_proxy"].get(data.get("budget_proxy", "unknown"), 0)
    score += WEIGHTS["language_match_bonus"].get(data.get("language_signal", "english_default"), 0)
    return int(max(0, min(10, round(score))))


def _combined_score(pain: int, adoption: int) -> int:
    """Weighted average, rounded to integer in [0,10]."""
    w = WEIGHTS["composite"]
    blended = (pain * w["pain"]) + (adoption * w["adoption"])
    return int(max(0, min(10, round(blended))))


def run(data: dict) -> dict:
    """Add pain_score, adoption_score, lead_score, lead_priority to the lead dict."""
    pain = _pain_score(data)
    adoption = _adoption_score(data)
    score = _combined_score(pain, adoption)

    if score >= 7:
        priority = "high"
    elif score >= 4:
        priority = "medium"
    else:
        priority = "low"

    return {
        **data,
        "pain_score": pain,
        "adoption_score": adoption,
        "lead_score": score,
        "lead_priority": priority,
    }
