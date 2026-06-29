"""
modules/reply_router.py - reply gap-fill for WhatsApp + Instagram (feature e).

The reply-bot audit (scripts/audit_reply_coverage.py) shows email has a full
classify -> draft -> send loop (reply_handler) but WhatsApp and Instagram have
no LLM drafting: whatsapp.send_pitch is a fixed string and instagram has no
reply path at all. This module fills that gap by reusing social_brain (the same
free Groq classify + craft_reply the social agent uses) so WhatsApp and
Instagram inbound get a human, on-voice DRAFT.

SAFETY (user is a minor, nothing may auto-dial, social auto-send stays off):
  - DRAFT-by-default. route_* always returns the draft; it only SENDS when the
    explicit per-channel autosend env flag is set to "1":
        OUTREACH_AUTOSEND_WHATSAPP=1   (default OFF)
        OUTREACH_AUTOSEND_INSTAGRAM=1  (default OFF)
  - The flag is read at call time (not import) so a test/operator can flip it
    per run. Whitelist check: only the exact string "1" enables sending.
  - No paid model: drafting is Groq via social_brain, never Anthropic/OpenAI.

This module never raises on the LLM path - social_brain.classify / craft_reply
already fall back to safe human text when GROQ_API_KEY is unset.
"""
from __future__ import annotations

import logging
import os

from modules import social_brain

log = logging.getLogger(__name__)

WHATSAPP = "whatsapp"
INSTAGRAM = "instagram"


def _autosend_enabled(channel: str) -> bool:
    """True only when OUTREACH_AUTOSEND_<CHANNEL> is exactly '1'. Default OFF."""
    return os.getenv(f"OUTREACH_AUTOSEND_{channel.upper()}", "0").strip() == "1"


def draft_reply(text: str, author: str = "", niche: str = "") -> dict:
    """Classify an inbound social message and draft a reply with social_brain.

    Returns {category, niche, intent, summary, draft}. Pure drafting - no send,
    no network beyond the (free) Groq call inside social_brain, which itself
    degrades to a safe fallback when GROQ_API_KEY is unset.
    """
    classification = social_brain.classify(text or "", author=author)
    if niche and not classification.get("niche"):
        classification["niche"] = niche
    draft = social_brain.craft_reply(text or "", classification, author=author)
    return {**classification, "draft": draft}


def route_whatsapp(text: str, to_mobile: str = "", author: str = "",
                   niche: str = "", autosend: bool | None = None) -> dict:
    """Draft (and optionally send) a WhatsApp reply.

    autosend=None  -> use the OUTREACH_AUTOSEND_WHATSAPP env flag (default OFF).
    A send only happens inside a live 24h window (whatsapp.send_freeform), and
    only when a real to_mobile is supplied.
    """
    result = draft_reply(text, author=author, niche=niche)
    do_send = _autosend_enabled(WHATSAPP) if autosend is None else bool(autosend)
    sent = False
    if do_send and result["draft"] and to_mobile:
        try:
            from modules import whatsapp
            sent = whatsapp.send_freeform(to_mobile, result["draft"])
        except Exception as e:
            log.error("route_whatsapp send failed: %s", e)
            sent = False
    else:
        log.info("WhatsApp reply DRAFTED (autosend off) for @%s: %s",
                 author or "anon", result["draft"][:80])
    return {**result, "channel": WHATSAPP, "autosend": do_send, "sent": sent}


def route_instagram(text: str, handle: str = "", author: str = "",
                    niche: str = "", autosend: bool | None = None) -> dict:
    """Draft (and optionally send) an Instagram DM reply.

    autosend=None -> use the OUTREACH_AUTOSEND_INSTAGRAM env flag (default OFF).
    Sending also still requires instagram.py's own INSTAGRAM_ENABLED gate, so
    this is double-gated by design.
    """
    result = draft_reply(text, author=author, niche=niche)
    do_send = _autosend_enabled(INSTAGRAM) if autosend is None else bool(autosend)
    sent = False
    if do_send and result["draft"] and handle:
        try:
            from modules import instagram
            # Reuse instagram.send by passing the draft as instagram_msg; the
            # module stays the single owner of the instagrapi call + daily caps.
            sent = instagram.send({
                "instagram_handle": handle,
                "instagram_msg": result["draft"],
                "company_name": author or handle,
            })
        except Exception as e:
            log.error("route_instagram send failed: %s", e)
            sent = False
    else:
        log.info("Instagram reply DRAFTED (autosend off) for @%s: %s",
                 handle or author or "anon", result["draft"][:80])
    return {**result, "channel": INSTAGRAM, "autosend": do_send, "sent": sent}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sample = "Hey we keep missing calls at our clinic, how much per month?"
    print("WHATSAPP:", route_whatsapp(sample, author="dr_sharma", niche="dental"))
    print("INSTAGRAM:", route_instagram(sample, handle="smileclinic", niche="dental"))
