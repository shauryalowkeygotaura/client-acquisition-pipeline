"""
modules/icebreaker.py — Lead-specific opening line for local clinics & schools.

Produces ONE highly specific icebreaker per lead from its Google Maps facts
(name, locality, rating, review count, niche, hours) tied to the missed-call
angle — because the product is a phone receptionist, the lead's phone line IS
the pitch surface.

Groq only (per project rule). Static instruction block is kept BEFORE any
interpolated value so Groq's prompt-cache prefix matches across leads.
Degrades to a deterministic template if the LLM is unavailable — never blocks
the harvest.
"""
import json
import logging
import os
import re

from openai import OpenAI

from config import LLM_BASE_URL, LLM_MODEL

log = logging.getLogger(__name__)

LLM_API_KEY = os.getenv("GROQ_API_KEY")

# Static, cacheable prefix. No interpolation above this line.
_RULES = """You write ONE opening line for a cold WhatsApp/DM to the owner of a small
local business in India. The sender, Shaurya, builds phone receptionists that pick up
missed calls and book appointments.

The opener's only job: prove "a real person looked at MY business specifically", then
nudge gently toward the missed-call problem. It is NOT a pitch.

HARD RULES:
- 1 to 2 sentences. Under 35 words total.
- Open with a concrete fact about THIS business: its name, its locality/area, its
  Google rating + review count, or what it does. Never generic.
- Tie naturally to phone calls: after-hours calls, lunch-hour calls, calls during
  class/treatment, calls that ring out when the front desk is busy.
- Plain Indian English. Hinglish is fine if it sounds natural. Sound like a person
  typing at a desk, not a brochure.
- NO em dashes. Use commas or full stops. NO buzzwords (AI, solution, leverage,
  seamless, automation, optimize). No "I hope", no "I came across", no "I wanted to".
- Do not invent facts. Use only the data given. If rating/reviews are blank, lean on
  name + area + what they do.

Return ONLY JSON: {"icebreaker": "<the line>"}"""


def _locality(address: str) -> str:
    """Pull a human area name out of a full address (best effort)."""
    if not address:
        return ""
    # First comma-chunk that isn't a pure number / pincode.
    for part in address.split(","):
        p = part.strip()
        if p and not re.fullmatch(r"[\d\s\-]+", p) and len(p) > 2:
            return p
    return ""


def _fallback(data: dict) -> str:
    name = data.get("company_name", "your practice")
    area = _locality(data.get("address", "")) or data.get("location", "")
    rating = data.get("rating", "")
    reviews = data.get("review_count", "")
    if rating and reviews:
        base = f"Saw {name} has {rating} stars across {reviews} reviews"
    elif area:
        base = f"Saw {name} over in {area}"
    else:
        base = f"Saw {name}"
    return f"{base}, so the phone must ring a fair bit. Curious how many calls slip through after hours or during busy slots."


def _generate(data: dict) -> str:
    if not LLM_API_KEY:
        return _fallback(data)

    facts = {
        "name": data.get("company_name", ""),
        "what_they_do": data.get("business_type") or data.get("maps_niche") or data.get("niche", ""),
        "locality": _locality(data.get("address", "")),
        "city": data.get("location", ""),
        "google_rating": data.get("rating", ""),
        "review_count": data.get("review_count", ""),
        "hours": data.get("hours", ""),
        "services": data.get("services", ""),
    }
    facts = {k: v for k, v in facts.items() if v not in ("", None)}

    user = f"BUSINESS FACTS (JSON):\n{json.dumps(facts, ensure_ascii=False)}"

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _RULES},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=160,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        line = str(json.loads(raw).get("icebreaker", "")).strip()
        line = line.replace("—", ", ").replace(" - ", ", ")  # enforce no em dashes
        return line or _fallback(data)
    except Exception as e:
        log.warning("Icebreaker generation failed for %s: %s",
                    data.get("company_name", "?"), e)
        return _fallback(data)


def run(data: dict) -> dict:
    """Attach `icebreaker` to the lead. Also seeds company_hook so the generator
    (if run later for email/DM bodies) opens with the same specific line."""
    line = _generate(data)
    out = {**data, "icebreaker": line}
    if not out.get("company_hook"):
        out["company_hook"] = line
    return out
