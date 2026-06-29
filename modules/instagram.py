"""
modules/instagram.py — v3 Instagram DM channel

For Indian SMB healthcare (dental, medical, physio), Instagram is the primary
inbound channel — owners post clinic photos, patient testimonials, and offers
there. LinkedIn is dead. Email is mid. Instagram + WhatsApp is where the
attention actually is.

This is a STUB for now: it implements the same `send(data) -> bool` interface
as linkedin.py and whatsapp.py so the pipeline routing can wire it in, but the
actual instagrapi calls are guarded behind INSTAGRAM_ENABLED so we don't burn
through Instagram's rate limits before we're ready.

To enable in production:
  1. Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in Doppler.
  2. Set INSTAGRAM_ENABLED=1.
  3. Pre-warm the account (post 5–10 photos, follow 20 dental accounts, like 50 posts).
  4. Hard cap below: 20 DMs/day to avoid soft-ban.
"""
import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
INSTAGRAM_ENABLED = os.getenv("INSTAGRAM_ENABLED") == "1"

# Conservative daily cap — Instagram soft-bans accounts that send >50 DMs/day
# from cold reputation. 20/day for the first 30 days, then ramp.
DAILY_DM_LIMIT = int(os.getenv("INSTAGRAM_DAILY_DM_LIMIT", "20"))
MIN_DELAY_SECONDS = int(os.getenv("INSTAGRAM_MIN_DELAY", "45"))

# Hide each thread from Shaurya's inbox right after sending, so only leads who
# REPLY resurface (IG re-shows a hidden thread on the next inbound message).
# Default on. Set INSTAGRAM_HIDE_AFTER_SEND=0 to keep sent threads visible.
HIDE_AFTER_SEND = os.getenv("INSTAGRAM_HIDE_AFTER_SEND", "1") == "1"

_send_counts: dict[str, int] = {}  # date_str → count sent today


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _within_daily_limit() -> bool:
    return _send_counts.get(_today(), 0) < DAILY_DM_LIMIT


def _increment_count() -> None:
    today = _today()
    _send_counts[today] = _send_counts.get(today, 0) + 1


def resolve_handle(data: dict) -> str | None:
    """
    Resolve a usable @handle from the lead dict.
    Order: explicit instagram_handle → scraped from website → None.

    Researcher should populate `instagram_handle` when it finds the
    instagram.com/* URL in the homepage. Falls back to None if missing.
    """
    handle = (data.get("instagram_handle") or "").strip().lstrip("@")
    if handle:
        return handle
    # Try to extract from any URL field
    for key in ("instagram_url", "social_links"):
        val = data.get(key) or ""
        if "instagram.com/" in val:
            tail = val.split("instagram.com/", 1)[1]
            handle = tail.split("/", 1)[0].split("?", 1)[0].strip()
            if handle:
                return handle
    return None


def send(data: dict) -> bool:
    """
    Send an Instagram DM. Returns True on success.

    NO-OP unless INSTAGRAM_ENABLED=1. This is deliberate — we wire the
    routing first, then enable the channel once an account is warmed.
    """
    if not INSTAGRAM_ENABLED:
        log.debug("instagram.send skipped — INSTAGRAM_ENABLED is not 1")
        return False

    if not (INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD):
        log.warning("INSTAGRAM_USERNAME/PASSWORD not set — skipping send")
        return False

    handle = resolve_handle(data)
    if not handle:
        log.info("No Instagram handle for %s — skipping IG DM", data.get("company_name"))
        return False

    if not _within_daily_limit():
        log.warning("Instagram daily DM limit (%d) reached — queuing for tomorrow", DAILY_DM_LIMIT)
        return False

    message = data.get("instagram_msg") or data.get("linkedin_msg") or ""
    if not message:
        log.warning("No instagram_msg for %s — skipping", handle)
        return False

    try:
        from instagrapi import Client  # type: ignore

        cl = Client()
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
        user_id = cl.user_id_from_username(handle)
        dm = cl.direct_send(message[:1000], [user_id])  # 1000-char hard cap

        # Clean-inbox pattern: hide the thread right after sending. Only leads
        # who reply will resurface it, so the IG inbox becomes "repliers only".
        if HIDE_AFTER_SEND:
            # direct_send returns a DirectMessage object (most versions) or a
            # dict — handle both so a shape change never crashes a real send.
            thread_id = getattr(dm, "thread_id", None)
            if thread_id is None and isinstance(dm, dict):
                thread_id = dm.get("thread_id")
            if thread_id:
                try:
                    cl.direct_thread_hide(thread_id)
                    log.debug("Hid IG thread %s for @%s after send", thread_id, handle)
                except Exception as e:
                    log.warning("Sent to @%s but could not hide thread: %s", handle, e)

        time.sleep(MIN_DELAY_SECONDS)  # pacing — keeps activity human-shaped
        _increment_count()
        log.info("Instagram DM sent to @%s (%s)", handle, data.get("company_name"))
        return True
    except ImportError:
        log.error("instagrapi not installed — pip install instagrapi")
        return False
    except Exception as e:
        log.error("Instagram DM failed for @%s: %s", handle, e)
        return False


# ── Distribution (feature f): cold video DM / warm link, DRAFT by default ────
# Double-gated: needs INSTAGRAM_ENABLED=1 (account warmed) AND
# OUTREACH_AUTOSEND_INSTAGRAM=1 (explicit opt-in) before anything sends. Cold =
# a video DM (IG shadow-filters cold DMs that contain links into Requests); warm
# = the /demo/<slug> link, only after a reply so the thread is already open. The
# cold/warm rule lives in modules/delivery, not here.

def _autosend() -> bool:
    return os.getenv("OUTREACH_AUTOSEND_INSTAGRAM", "0").strip() == "1"


def _send_video(handle: str, path, caption: str = "") -> bool:
    """Send an mp4 as an Instagram video DM via instagrapi. NO-OP unless the
    channel is enabled + credentialed. Returns False on any failure."""
    from pathlib import Path as _Path
    if not INSTAGRAM_ENABLED or not (INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD):
        return False
    if not _within_daily_limit():
        log.warning("Instagram daily DM limit (%d) reached - video queued", DAILY_DM_LIMIT)
        return False
    mp4 = _Path(path)
    if not mp4.exists():
        log.warning("Instagram video missing: %s", path)
        return False
    try:
        from instagrapi import Client  # type: ignore

        cl = Client()
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
        user_id = cl.user_id_from_username(handle)
        cl.direct_send_video(str(mp4), [user_id])
        if caption:
            cl.direct_send(caption[:1000], [user_id])
        time.sleep(MIN_DELAY_SECONDS)
        _increment_count()
        log.info("Instagram video DM sent to @%s", handle)
        return True
    except ImportError:
        log.error("instagrapi not installed - pip install instagrapi")
        return False
    except Exception as e:
        log.error("Instagram video DM failed for @%s: %s", handle, e)
        return False


def deliver(data: dict, autosend: bool | None = None) -> dict:
    """Attach the right asset to an Instagram outreach, per the two-state rule.

    DRAFT by default: returns what WOULD be sent and only transmits when BOTH
    INSTAGRAM_ENABLED=1 and the OUTREACH_AUTOSEND_INSTAGRAM flag (or an explicit
    autosend=True) are set.
    """
    from modules import delivery
    asset = delivery.instagram_asset(data)
    do_send = _autosend() if autosend is None else bool(autosend)
    result = {**asset, "autosend": do_send, "sent": False}

    if asset["kind"] == "none":
        return result
    handle = resolve_handle(data)
    if not do_send or not handle:
        log.info("Instagram %s DRAFTED (autosend off or no handle) for %s",
                 asset["kind"], data.get("company_name"))
        return result

    if asset["kind"] == "link":
        result["sent"] = send({
            "instagram_handle": handle,
            "instagram_msg": f"{asset['line']} {asset['url']}",
            "company_name": data.get("company_name", handle),
        })
    elif asset["kind"] == "video":
        result["sent"] = _send_video(handle, asset["path"])
    return result
