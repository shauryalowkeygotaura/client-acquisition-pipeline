"""
modules/social_brain.py

The "reply + qualify" half of the agent, generalized to ANY platform. Given an
inbound item it:
  1. classify() — Groq sorts it into lead / engage / ignore and pulls niche +
     intent. This is the same idea as inbound_intake._qualify, kept here so the
     social agent stays self-contained and testable without Google Sheets.
  2. craft_reply() — Groq writes the actual reply, in a register that matches
     the category:
        lead   → consultative, offer the 2-min clip, no hard pitch (mirrors the
                 reply_handler Hormozi ladder, stage "initial").
        engage → warm, human, peer-to-peer; answer briefly, invite more.

Both prompts put their static block FIRST for Groq cache hits.

Qualified leads can optionally be persisted to the existing leads sheet via
inbound_intake, but that is the orchestrator's call (and is guarded), so this
module has no hard dependency on Sheets credentials.
"""
from __future__ import annotations

import json
import logging
import os

from config import LLM_BASE_URL, LLM_MODEL

log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

LEAD = "lead"
ENGAGE = "engage"
IGNORE = "ignore"

_CLASSIFY_SYSTEM = """
You triage inbound social messages for a brand that sells AI voice
receptionists to small businesses (clinics, dental, salons, gyms, etc.).
Sort each message into exactly one category:
  - lead: a business owner/operator/decision-maker asking about the service, or
    describing a pain it solves ("we miss half our calls", "how much per month").
  - engage: a genuine human worth a friendly reply — a peer, a viewer, someone
    asking a technical or curious question. Not a buyer, but worth talking to.
  - ignore: spam, bots, pure vanity ("nice!"), recruiters, off-topic, abuse.
Also extract:
  - niche: dental/medical/salon/gym/restaurant/clinic/education/other/unknown
  - intent: low/medium/high (explicit buying intent = high)
  - summary: one short sentence of what they want.
Return ONLY valid JSON with keys: category, niche, intent, summary.
""".strip()

_REPLY_SYSTEM = """
You write replies on social platforms for Shaurya, who builds AI voice
receptionists for small businesses. Voice rules, always:
- Real human, not a marketer. Plain words. No buzzwords, no "I hope this finds".
- Never use em dashes. Use commas, colons, or parentheses.
- 15-55 words. One idea. Sound like a quick, friendly DM reply.
- If category is "lead": be consultative, acknowledge their specific pain, and
  offer to send a 2-minute clip of the agent handling a real call. Do NOT ask
  for a call yet and do NOT hard-pitch price.
- If category is "engage": answer their question briefly and honestly, like a
  peer. No selling. End with a light question to keep it going.
- Sign nothing. No "Best,". Just the message.
- Banned patterns (stop-slop): "not X, it's Y" contrasts, "isn't just",
  "Here's the thing", adverb openers (Honestly, Literally, Actually),
  "game-changer", "love this". State the point in plain active voice.
Return ONLY the reply text.
""".strip()


def _client():
    from openai import OpenAI
    return OpenAI(api_key=GROQ_API_KEY, base_url=LLM_BASE_URL)


def classify(text: str, author: str = "", client=None) -> dict:
    """Triage one inbound message. Never raises; falls back to 'engage'."""
    fallback = {"category": ENGAGE, "niche": "unknown", "intent": "low",
                "summary": text[:120]}
    if not GROQ_API_KEY and client is None:
        return fallback
    client = client or _client()
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": f"From @{author or 'anon'}:\n{text[:1500]}"},
            ],
            temperature=0.2,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        if data.get("category") not in (LEAD, ENGAGE, IGNORE):
            data["category"] = ENGAGE
        return {**fallback, **data}
    except Exception as e:
        log.error("classify failed: %s", e)
        return fallback


def craft_reply(text: str, classification: dict, author: str = "", client=None) -> str:
    """Write the reply for an inbound item, in the register its category wants.
    Never raises; returns a safe human fallback."""
    category = classification.get("category", ENGAGE)
    if category == LEAD:
        fallback = ("Totally hear you on the missed calls. Want me to send a "
                    "2-min clip of the agent handling a real one so you can judge it?")
    else:
        fallback = "Appreciate you reaching out. What are you working on?"

    if not GROQ_API_KEY and client is None:
        return fallback
    client = client or _client()
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _REPLY_SYSTEM},
                {"role": "user", "content": (
                    f"Category: {category}\n"
                    f"Niche: {classification.get('niche', 'unknown')}\n"
                    f"They said (from @{author or 'anon'}): {text[:800]}\n\n"
                    "Write the reply."
                )},
            ],
            temperature=0.6,
            max_tokens=160,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or fallback
    except Exception as e:
        log.error("craft_reply failed: %s — using fallback", e)
        return fallback
