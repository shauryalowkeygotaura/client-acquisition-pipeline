"""
modules/inbound_intake.py

Entry point for inbound leads that arrive via personal-brand surfaces:
  - @revengineee Instagram DMs
  - @revengine.notes Instagram DMs
  - Shaurya Shandilya LinkedIn DMs
  - revengine.beehiiv.com newsletter replies
  - YouTube comments / messages on @Revengine

Cold outbound leads enter the pipeline via scraper -> researcher -> enricher
-> scorer. Inbound leads skip the front of that chain because:
  - We didn't find them; they found us
  - They self-identify in the message itself
  - They are already warmer than the warmest outbound lead
  - The cold-opener has no purpose; the conversation is already started

Flow:
  intake(message_dict)
    -> qualify()      # LLM classifies real lead vs vanity DM vs not-a-lead
    -> attribute()    # capture which post / surface drove the contact
    -> save row in sheet with conversation_stage="warm", source="inbound-<surface>"
    -> hand off to reply_handler._generate_response() for the first reply

The handoff means inbound leads use the same Hormozi CTA ladder + objection
rebuttals + classifier as outbound replies. One sales mechanic, two entry points.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from openai import OpenAI

from config import LLM_BASE_URL, LLM_MODEL
from modules import reply_handler, sheets_writer
from modules.security_utils import get_audit_log

log = logging.getLogger(__name__)
audit = get_audit_log()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

VALID_SURFACES = {
    "ig-dm-main",       # @revengineee Instagram DMs
    "ig-dm-notes",      # @revengine.notes Instagram DMs
    "linkedin-dm",      # Shaurya Shandilya LinkedIn DMs
    "beehiiv-reply",    # revengine.beehiiv.com newsletter replies
    "youtube-comment",  # YouTube comments / DMs
    "cold-email-cold",  # cold email where they reached out first (rare)
    "manual",           # manually entered after a conversation in person / call
}

_QUALIFY_PROMPT = """
You are qualifying an inbound message to decide if the sender is a real
potential client for an AI voice receptionist service targeting Indian
small businesses (clinics, salons, gyms, dental, physio, etc.).

Inbound message:
{message}

Sender identity (from the platform):
  Handle: {handle}
  Name: {name}
  Bio: {bio}

Surface: {surface}

Classify into exactly ONE category:
  - qualified_lead: clearly a business owner / operator / decision-maker
    asking about the service OR sharing a pain that the service solves.
    Examples: "do you build this for clinics?", "how much per month?",
    "we get 200 calls a day and miss half".
  - curious_creator: an indie builder, peer, or content viewer asking
    technical questions but unlikely to buy. Examples: "what stack do
    you use?", "love the build log".
  - vanity_dm: compliments, emojis, "great work", "follow back", "let's
    collab" with no real business signal.
  - not_relevant: spam, off-topic, recruiter outreach, course-pitching,
    random.

Also extract:
  - niche: one of dental / medical / salon / gym / restaurant / clinic /
    legal / education / other / unknown. Best guess from message + bio.
  - location_hint: city or "unknown".
  - pain_signal: 1 sentence summary of the pain they mentioned, if any.
    Empty string if none.
  - intent_strength: low / medium / high. High = explicit buying intent.

Return ONLY valid JSON with keys: category, niche, location_hint,
pain_signal, intent_strength.
""".strip()


def _qualify(message: str, identity: dict, surface: str) -> dict:
    """Classify an inbound message. Returns the classification dict."""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set — defaulting to curious_creator")
        return {
            "category": "curious_creator",
            "niche": "unknown",
            "location_hint": "unknown",
            "pain_signal": "",
            "intent_strength": "low",
        }

    client = OpenAI(api_key=GROQ_API_KEY, base_url=LLM_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": _QUALIFY_PROMPT.format(
                message=message[:1500],
                handle=identity.get("handle", "?"),
                name=identity.get("name", "?"),
                bio=identity.get("bio", "")[:300],
                surface=surface,
            )}],
            temperature=0.2,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as e:
        log.error("inbound_intake qualify failed: %s", e)
        return {
            "category": "curious_creator",
            "niche": "unknown",
            "location_hint": "unknown",
            "pain_signal": "",
            "intent_strength": "low",
        }


def _slug_from_identity(identity: dict, surface: str) -> str:
    """Build a stable slug from the inbound identity."""
    base = (
        identity.get("handle")
        or identity.get("email")
        or identity.get("name")
        or "unknown"
    )
    base = re.sub(r"[^a-zA-Z0-9-]+", "-", base.lower()).strip("-")
    return f"inbound-{surface}-{base}"[:60]


def intake(
    message: str,
    surface: str,
    identity: dict,
    attribution_post_id: str = "",
    raw_meta: dict | None = None,
) -> dict:
    """
    Accept one inbound message. Decide if it's a lead. If yes, create
    a sheet row and prep it for the existing reply_handler pipeline.

    Args:
        message: the raw text the prospect sent.
        surface: one of VALID_SURFACES.
        identity: dict with at least one of {handle, name, email, bio,
            linkedin_url, website}. More signals = better qualification.
        attribution_post_id: the Reel / carousel / post slug that drove
            the contact, if known. Empty string if untracked.
        raw_meta: optional metadata pulled from the source platform
            (timestamp, message id, conversation id, etc.).

    Returns:
        dict with keys: action ("qualified" | "skipped" | "duplicate"),
        slug, classification, reason.
    """
    if surface not in VALID_SURFACES:
        log.error("invalid surface: %s", surface)
        return {"action": "skipped", "reason": f"invalid surface {surface!r}",
                "slug": "", "classification": {}}

    classification = _qualify(message, identity, surface)
    category = classification.get("category", "curious_creator")
    intent = classification.get("intent_strength", "low")

    if category == "not_relevant":
        audit.append("inbound_intake", "skip", identity.get("handle", "?"),
                     ok=True, detail={"reason": "not_relevant", "surface": surface})
        return {"action": "skipped", "reason": "not_relevant",
                "slug": "", "classification": classification}

    if category == "vanity_dm":
        audit.append("inbound_intake", "skip", identity.get("handle", "?"),
                     ok=True, detail={"reason": "vanity_dm", "surface": surface})
        return {"action": "skipped", "reason": "vanity_dm",
                "slug": "", "classification": classification}

    # curious_creator: log but do not create a sales row. The brand
    # gets the engagement; the sales pipeline does not get polluted.
    if category == "curious_creator":
        audit.append("inbound_intake", "log", identity.get("handle", "?"),
                     ok=True, detail={"reason": "curious_creator",
                                      "surface": surface,
                                      "post_id": attribution_post_id})
        return {"action": "skipped", "reason": "curious_creator",
                "slug": "", "classification": classification}

    # qualified_lead: create the sheet row
    slug = _slug_from_identity(identity, surface)

    existing = sheets_writer.get_all_leads()
    if any((row.get("slug") or "") == slug for row in existing):
        audit.append("inbound_intake", "duplicate", slug,
                     ok=True, detail={"surface": surface})
        return {"action": "duplicate", "reason": "already in sheet",
                "slug": slug, "classification": classification}

    intent_bonus = {"high": 4, "medium": 2, "low": 1}.get(intent, 1)

    data = {
        "slug": slug,
        "company_name": identity.get("name") or identity.get("handle") or "inbound",
        "website": identity.get("website", ""),
        "domain": identity.get("website", "").replace("https://", "").replace("http://", "").split("/")[0],
        "contact_name": identity.get("name", ""),
        "email": identity.get("email", ""),
        "linkedin_url": identity.get("linkedin_url", ""),
        "niche": classification.get("niche", "unknown"),
        "location": classification.get("location_hint", "unknown"),
        "lead_score": 7 + intent_bonus,           # inbound starts at 7+ ("high")
        "lead_priority": "high",
        "hiring_urgency": "medium",
        "pain_signal": classification.get("pain_signal", ""),
        "channel_used": surface,
        "conversation_stage": "warm",             # skip initial / follow_up_1
        "reply_status": "interested",             # they reached out → interested
        "status": "inbound_warm",
        "inbound_source": surface,
        "attribution_post_id": attribution_post_id,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }

    saved = sheets_writer.save(data, existing)
    if not saved:
        return {"action": "skipped", "reason": "sheets save failed",
                "slug": slug, "classification": classification}

    audit.append("inbound_intake", "qualified", slug, ok=True,
                 detail={"surface": surface, "category": category,
                         "intent": intent, "niche": classification.get("niche"),
                         "post_id": attribution_post_id})

    log.info("Inbound lead captured: %s (%s, intent=%s, post=%s)",
             slug, surface, intent, attribution_post_id or "untracked")

    return {"action": "qualified", "reason": "captured",
            "slug": slug, "classification": classification}


def respond_to_inbound(slug: str, original_message: str) -> bool:
    """
    After intake captures a qualified inbound lead, generate and (optionally)
    send the first response using the existing reply_handler machinery.

    For now this only generates the response text and stages it. Actual
    send-back must use the platform-specific sender (Instagram DM, LinkedIn,
    etc.) which is the caller's responsibility because each surface has
    different auth + rate-limit semantics.
    """
    leads = sheets_writer.get_all_leads()
    lead = next((l for l in leads if l.get("slug") == slug), None)
    if not lead:
        log.error("respond_to_inbound: slug not found: %s", slug)
        return False

    classification = {
        "category": "interested",
        "objection_type": "",
        "brief_summary": (lead.get("pain_signal") or original_message)[:200],
    }

    response = reply_handler._generate_response(  # type: ignore[attr-defined]
        lead, classification, reply_text=original_message
    )

    # Stash the generated reply on the sheet so a human can review/send
    # via whatever platform the lead came in on. Avoids accidental cross-
    # platform auto-sends.
    try:
        sheets_writer.update_field(slug, "linkedin_msg", response[:1500])
    except Exception as e:
        log.warning("could not store inbound response for review: %s", e)

    audit.append("inbound_intake", "respond_prepared", slug, ok=True,
                 detail={"length": len(response)})
    return True
