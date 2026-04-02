"""
modules/enricher.py — Phase 1 upgrade

Adds structured enrichment fields to each lead dict using regex heuristics.
Zero extra API calls. Runs after researcher.py, before scorer.py.
"""
import re

# ── Niche detection ─────────────────────────────────────────────────────────

_NICHE_PATTERNS: dict[str, str] = {
    "dental":     r"\b(dental|dentist|orthodont|oral|teeth|endodont|periodont)\b",
    "medical":    r"\b(medical|clinic|doctor|physician|gp|general practice|healthcare|health cent(er|re)|hospital)\b",
    "physio":     r"\b(physio|physical therapy|rehab|rehabilitation|chiropractic|osteopath)\b",
    "legal":      r"\b(law firm|legal|attorney|solicitor|barrister|advocate|counsel|chambers)\b",
    "optometry":  r"\b(optom|optic(ian)?|eye (care|clinic)|vision|glasses|contact lens)\b",
    "salon":      r"\b(salon|spa|beauty|hair|nail|wellness|barber|medi.?spa|aesthetic)\b",
    "school":     r"\b(school|academy|college|institute|education|tutoring|coaching|preschool|kindergarten)\b",
    "hotel":      r"\b(hotel|hospitality|resort|motel|inn|guest.?house|b&b)\b",
    "trades":     r"\b(plumb|electric|hvac|construct|contractor|builder|mechanic|automotive|garage)\b",
    "veterinary": r"\b(vet(erinar)?|animal (hospital|clinic)|pet care)\b",
}

# ── Urgency signals ──────────────────────────────────────────────────────────

_URGENCY_HIGH = re.compile(
    r"\b(urgent|urgently|immediately|asap|as soon as possible|right away|must start|"
    r"starting immediately|opening immediately|fill immediately|needed now|"
    r"critical|emergency hire|no delay)\b",
    re.IGNORECASE,
)
_URGENCY_LOW = re.compile(
    r"\b(no rush|flexible start|when available|eventually|future opening)\b",
    re.IGNORECASE,
)

# ── Pain signal detection ────────────────────────────────────────────────────

_PAIN_PATTERNS: dict[str, str] = {
    "scaling":      r"\b(growing|expansion|new location|new branch|increased demand|high volume)\b",
    "turnover":     r"\b(replace|replacement|previous (employee|staff)|no longer with|vacated|left the position)\b",
    "coverage_gap": r"\b(part.?time|temporary|interim|maternity|leave of absence|cover|gap|fill.?in)\b",
    "capacity":     r"\b(overwhelmed|overloaded|cannot manage|too (busy|many|much)|high call volume|"
                    r"can.?t keep up|struggling to answer)\b",
}

# ── Per-niche lookup tables ──────────────────────────────────────────────────

_CALL_VOLUME: dict[str, str] = {
    "dental": "high", "medical": "high", "legal": "high",
    "optometry": "high", "physio": "high", "veterinary": "high",
    "salon": "medium", "trades": "medium", "hotel": "medium",
    "school": "low",
}

_REVENUE_DEP: dict[str, str] = {
    "dental": "high", "medical": "high", "legal": "high",
    "physio": "high", "salon": "high", "optometry": "high",
    "veterinary": "high", "trades": "high",
    "hotel": "medium",
    "school": "low",
}


def _classify_niche(text: str) -> str:
    for niche, pattern in _NICHE_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return niche
    return "general"


def _classify_urgency(text: str) -> str:
    if _URGENCY_HIGH.search(text):
        return "high"
    if _URGENCY_LOW.search(text):
        return "low"
    return "medium"


def _classify_pain(text: str) -> str:
    for pain, pattern in _PAIN_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return pain
    return "general_need"


def run(data: dict) -> dict:
    """Enrich a lead dict with niche + urgency + pain_signal fields."""
    # Combine all text signals available at this stage
    corpus = " ".join(filter(None, [
        data.get("job_description_text", ""),
        data.get("scraped_details", ""),
        data.get("company_name", ""),
        data.get("services", ""),
        data.get("job_title", ""),
    ]))

    niche = _classify_niche(corpus)
    urgency = _classify_urgency(corpus)
    pain = _classify_pain(corpus)

    return {
        **data,
        "niche": niche,
        "hiring_urgency": urgency,
        "pain_signal": pain,
        "likely_call_volume": _CALL_VOLUME.get(niche, "medium"),
        "revenue_dependency_on_calls": _REVENUE_DEP.get(niche, "medium"),
    }
