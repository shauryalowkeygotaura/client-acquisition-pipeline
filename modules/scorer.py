"""
modules/scorer.py — Phase 2 upgrade

Deterministic 0–10 lead scoring. Runs after enricher.py.
No API calls. Weights are tunable in WEIGHTS dict below.

Score breakdown (max 10):
  hiring_urgency       0–4
  likely_call_volume   0–3
  revenue_dependency   0–2
  niche_bonus          -1 to +1
"""

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
}


def run(data: dict) -> dict:
    """Add lead_score (0–10) and lead_priority (high/medium/low) to the lead dict."""
    score = 0
    score += WEIGHTS["hiring_urgency"].get(data.get("hiring_urgency", "medium"), 2)
    score += WEIGHTS["likely_call_volume"].get(data.get("likely_call_volume", "medium"), 2)
    score += WEIGHTS["revenue_dependency_on_calls"].get(
        data.get("revenue_dependency_on_calls", "medium"), 1
    )
    score += WEIGHTS["niche_bonus"].get(data.get("niche", "general"), 0)
    score = max(0, min(10, score))

    if score >= 7:
        priority = "high"
    elif score >= 4:
        priority = "medium"
    else:
        priority = "low"

    return {**data, "lead_score": score, "lead_priority": priority}
