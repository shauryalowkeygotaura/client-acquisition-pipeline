"""
modules/delivery.py - the distribution integration (feature f, the headline).

ONE place that decides what asset rides with an outreach message, per the
integration contract's channelDelivery rule. Two states, never more:

  COLD first-touch (no reply yet): give value, ask only for a reply.
    - Attach/embed the personalized VIDEO. For highly rated prospects
      (video_pitch.is_probable_client) it is rendered on first touch if missing;
      low-probability leads get no video so render compute is not wasted.
    - NEVER a link. A raw cold link is the top phishing signal - it is the one
      thing this module exists to prevent.

  WARM / replied (reply_status==interested, or stage promoted to warm/booked,
  or booked_call==yes): give the thing they asked to see.
    - The interactive /demo/<slug> LINK, as clean path-based anchor text, one
      link maximum. No video re-attach.

The cold/warm test is centralized here so email_sender, whatsapp and instagram
all agree. The outreach modules call plan()/the per-channel helpers; they do not
re-derive the rule.

Everything here is FREE and draft-safe: the only asset is a local mp4 (attach)
or a deterministic string (link). Nothing here auto-sends on its own - the
outreach modules keep their existing credential + autosend gates.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from modules import demo_builder, video_pitch

log = logging.getLogger(__name__)

# Gmail caps attachments at 25 MB; keep cold mp4s comfortably under so the email
# never bounces on size. Over budget -> we attach nothing (still no link).
MAX_EMAIL_ATTACH_BYTES = int(os.getenv("DELIVERY_MAX_EMAIL_ATTACH_BYTES", str(12 * 1024 * 1024)))

# A lead counts as WARM when the conversation has opened. Kept aligned with
# video_pitch.is_qualified (interested / booked) plus the explicit warm stage.
_WARM_REPLY = {"interested"}
_WARM_STAGE = {"warm", "booked"}


def is_warm(lead: dict) -> bool:
    """True once the lead has replied positively or been promoted. Cold = not this."""
    if str(lead.get("reply_status", "")).strip().lower() in _WARM_REPLY:
        return True
    if str(lead.get("conversation_stage", "")).strip().lower() in _WARM_STAGE:
        return True
    if str(lead.get("stage", "")).strip().lower() in _WARM_STAGE:
        return True
    if str(lead.get("booked_call", "")).strip().lower() == "yes":
        return True
    return False


def _ensure_cold_video(lead: dict) -> Path | None:
    """Cold-path video for a highly rated prospect, rendered once on first touch.
    Pins the canonical slug (same as the demo spine) so the mp4 filename matches,
    then defers to video_pitch.ensure, which is idempotent + exception-safe and
    gated on video_pitch.is_probable_client. A low-probability cold lead gets None
    (no video, never a link), so render compute is spent only on probable clients."""
    pinned = {**lead, "slug": demo_builder.resolve_slug(lead)}
    return video_pitch.ensure(pinned)


def demo_url_for(lead: dict) -> str:
    """Clean path-based interactive demo link for the lead's slug."""
    return demo_builder.demo_url(demo_builder.resolve_slug(lead))


def plan(lead: dict) -> dict:
    """Resolve the single delivery decision for a lead.

    Returns:
      {"state": "warm", "attach_video": None, "link": "https://.../demo/<slug>"}
      {"state": "cold", "attach_video": Path|None, "link": None}

    Invariant: link is non-None ONLY in the warm state. A cold lead can never be
    handed a link by any channel.

    Second invariant (gate alignment): a link is sent ONLY when the personalized
    demo actually exists. warm_mockup writes demo_url back to the lead after it
    generates the per-lead config + video, so a populated demo_url is the proof
    that /demo/<slug> resolves to a PERSONALIZED agent and not the generic base.
    A lead that is warm but whose assets have not been built yet degrades to the
    cold path (video if present, never a link), so we never link to a demo that
    does not exist. This ties the link gate to warm_mockup -> video_pitch.is_qualified.
    """
    stored_link = str(lead.get("demo_url", "")).strip()
    if is_warm(lead) and stored_link:
        # Return the exact value we validated (what warm_mockup persisted), not a
        # regenerated one, so the gate checks and the link sent are the same string.
        return {"state": "warm", "attach_video": None, "link": stored_link}
    return {"state": "cold", "attach_video": _ensure_cold_video(lead), "link": None}


# ── Per-channel asset shaping (consumed by the outreach modules) ─────────────

def email_assets(lead: dict) -> dict:
    """What an outreach EMAIL should carry.

    Returns {"attach_path": Path|None, "anchor": (text, url)|None}.
      cold: a small mp4 attachment when one exists and fits the size budget;
            otherwise nothing (NO bare URL - protects deliverability).
      warm: a single descriptive anchor (text, url); no attachment.
    """
    p = plan(lead)
    if p["state"] == "warm":
        return {"attach_path": None,
                "anchor": ("Here is your interactive receptionist demo", p["link"])}
    attach = p["attach_video"]
    if attach is not None:
        try:
            if attach.stat().st_size > MAX_EMAIL_ATTACH_BYTES:
                log.info("delivery: %s over email attach budget (%d B) - sending text only",
                         attach.name, MAX_EMAIL_ATTACH_BYTES)
                attach = None
        except OSError:
            attach = None
    return {"attach_path": attach, "anchor": None}


def whatsapp_asset(lead: dict) -> dict:
    """What an outreach WHATSAPP message should carry.

      cold: {"kind": "video", "path": Path}  (native inline mp4; a cold link
            gets the number reported) - or {"kind": "none"} if no video yet.
      warm: {"kind": "link", "url": ..., "line": one human context line}.
    """
    p = plan(lead)
    if p["state"] == "warm":
        return {"kind": "link", "url": p["link"],
                "line": "Here is the live demo you can try right now:"}
    if p["attach_video"] is not None:
        return {"kind": "video", "path": p["attach_video"]}
    return {"kind": "none"}


def instagram_asset(lead: dict) -> dict:
    """What an outreach INSTAGRAM DM should carry. Same shape as whatsapp_asset.

      cold: video DM (cold links get shadow-filtered into Requests) or none.
      warm: link only, after a reply, so the thread is already open.
    """
    p = plan(lead)
    if p["state"] == "warm":
        return {"kind": "link", "url": p["link"],
                "line": "Here is the demo you asked about:"}
    if p["attach_video"] is not None:
        return {"kind": "video", "path": p["attach_video"]}
    return {"kind": "none"}
