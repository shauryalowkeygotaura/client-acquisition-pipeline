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

# ── v3 Digital maturity signals ──────────────────────────────────────────────
# Detected from the same scraped corpus. Cheap, deterministic, regex-only.

_PMS_PATTERNS: dict[str, str] = {
    "practo_ray":  r"\bpracto.?ray\b|practo prime|practo profile",
    "dentolize":   r"\bdentolize\b",
    "clove":       r"\bclove dental\b|clove.app",
    "cleardent":   r"\bcleardent\b",
    "carestack":   r"\bcarestack\b",
    "dentrix":     r"\bdentrix\b",
    "open_dental": r"\bopen.?dental\b",
    "cloud_pms":   r"\b(cloud.?based|cloud pms|cloud.?software)\b",
}

_WHATSAPP_PRESENCE = re.compile(
    r"\bwa\.me/|api\.whatsapp\.com|whatsapp.?(us|now|chat|business)|chat on whatsapp|"
    r"message on whatsapp\b",
    re.IGNORECASE,
)

_INSTAGRAM_PRESENCE = re.compile(
    r"instagram\.com/[A-Za-z0-9_.]+|instagr\.am/|@[a-z0-9_.]+\s*\(insta",
    re.IGNORECASE,
)

_ONLINE_BOOKING = re.compile(
    r"\b(book (online|now|an? appointment)|online booking|appointment booking|"
    r"reserve online|schedule online)\b|cal\.com/|calendly\.com/|zocdoc\.com/|"
    r"practo\.com/.*book",
    re.IGNORECASE,
)

_MULTI_LOCATION = re.compile(
    r"\b(branches?|locations?|across [A-Z]|multiple (clinics?|centers?|branches?|outlets?)|"
    r"our (clinics?|centers?|branches?))\b|"
    r"\b\d+\s*(clinics?|branches?|locations?|outlets?|centers?)\b",
    re.IGNORECASE,
)

_LANGUAGE_HINGLISH = re.compile(
    r"\b(hindi|hinglish|bilingual|multilingual|vernacular|regional language)\b",
    re.IGNORECASE,
)

# Budget proxy: signals that the business already spends on premium tools or ads.
_BUDGET_HIGH_SIGNALS = re.compile(
    r"\b(practo prime|practo profile|premium listing|sponsored|verified clinic|"
    r"google ads|sponsored ad)\b|"
    r"justdial.com/.*premium|sulekha.com/.*premium",
    re.IGNORECASE,
)


def _classify_pms(text: str) -> str:
    for name, pattern in _PMS_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "unknown"


def _classify_review_velocity(data: dict) -> str:
    """Estimate review velocity from any review count + recency data the researcher gathered."""
    count = 0
    for key in ("google_review_count", "review_count", "reviews_count"):
        try:
            count = max(count, int(data.get(key) or 0))
        except (ValueError, TypeError):
            pass
    if count >= 200:
        return "high"
    if count >= 50:
        return "medium"
    if count > 0:
        return "low"
    return "unknown"


def _classify_budget_proxy(text: str, data: dict) -> str:
    """High budget = pays for premium directory/PMS/ads. Low = no signals at all."""
    if _BUDGET_HIGH_SIGNALS.search(text):
        return "high"
    if data.get("pms_signal") and data["pms_signal"] != "unknown":
        # Has a real PMS = at least medium budget tolerance.
        return "medium"
    if data.get("online_booking"):
        return "medium"
    return "low"


def _compute_digital_maturity(data: dict) -> int:
    """0–10 score. Each signal contributes a fixed weight. Caps at 10."""
    score = 0
    if data.get("online_booking"):       score += 2
    if data.get("whatsapp_presence"):    score += 2
    if data.get("instagram_presence"):   score += 1
    if data.get("multi_location"):       score += 1
    if data.get("pms_signal") and data["pms_signal"] != "unknown": score += 2
    rv = data.get("review_velocity", "unknown")
    if rv == "high":                     score += 2
    elif rv == "medium":                 score += 1
    return min(10, score)


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
    """Enrich a lead dict with niche + urgency + pain_signal + v3 digital maturity fields."""
    # Combine all text signals available at this stage
    # Include homepage_html / website_text if researcher provides them — that's where
    # digital maturity signals (PMS names, WhatsApp links, etc.) actually live.
    corpus = " ".join(filter(None, [
        data.get("job_description_text", ""),
        data.get("scraped_details", ""),
        data.get("company_name", ""),
        data.get("services", ""),
        data.get("job_title", ""),
        data.get("homepage_html", ""),
        data.get("website_text", ""),
    ]))

    niche = _classify_niche(corpus)
    urgency = _classify_urgency(corpus)
    pain = _classify_pain(corpus)

    # v3 digital maturity signals
    pms = _classify_pms(corpus)
    whatsapp_present = bool(_WHATSAPP_PRESENCE.search(corpus))
    instagram_present = bool(_INSTAGRAM_PRESENCE.search(corpus))
    online_booking = bool(_ONLINE_BOOKING.search(corpus))
    multi_location = bool(_MULTI_LOCATION.search(corpus))
    language_signal = "hinglish" if _LANGUAGE_HINGLISH.search(corpus) else "english_default"

    enriched = {
        **data,
        "niche": niche,
        "hiring_urgency": urgency,
        "pain_signal": pain,
        "likely_call_volume": _CALL_VOLUME.get(niche, "medium"),
        "revenue_dependency_on_calls": _REVENUE_DEP.get(niche, "medium"),
        # v3 digital maturity
        "pms_signal": pms,
        "whatsapp_presence": whatsapp_present,
        "instagram_presence": instagram_present,
        "online_booking": online_booking,
        "multi_location": multi_location,
        "language_signal": language_signal,
    }
    enriched["review_velocity"] = _classify_review_velocity(enriched)
    enriched["budget_proxy"] = _classify_budget_proxy(corpus, enriched)
    enriched["digital_maturity_score"] = _compute_digital_maturity(enriched)
    return enriched
