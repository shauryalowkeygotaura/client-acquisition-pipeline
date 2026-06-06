"""
modules/whatsapp.py — WhatsApp outreach via Meta Cloud API (free tier)

Sequence (Hormozi 2-message rule):
  Message 1 — Template (cold, requires Meta approval):
    "Hi [name], are you still looking to fill the receptionist gap at [company]?"
    → question only. No pitch, no link, no sell.

  Message 2 — Freeform (auto-sent after they reply, 24h window opens):
    "I build voice agents for [niche] businesses — they answer calls and handle
     bookings automatically. Want me to send a 2-min clip? — Shaurya"
    → triggered by Meta webhook → handle_reply()

Setup (one-time):
  1. business.facebook.com → Create a Business → Add a WhatsApp account
  2. developers.facebook.com → Create App → Add WhatsApp product
  3. WhatsApp → Getting Started → note your Phone Number ID + temporary token
  4. Generate a permanent token:
       System Users → Create system user → Generate token → check whatsapp_business_messaging
  5. Add your real number under WhatsApp → Phone Numbers → Add phone number
  6. Submit cold outreach template (see TEMPLATE section below)
  7. Set up webhook endpoint (see WEBHOOK section below)

Required Doppler secrets:
  WHATSAPP_PHONE_NUMBER_ID   — from Meta dashboard (numeric ID, not the phone number)
  WHATSAPP_ACCESS_TOKEN      — permanent system user token
  WHATSAPP_TEMPLATE_NAME     — name of your approved template (default: receptionist_outreach)

TEMPLATE to submit for approval in Meta Business Manager:
  Name:     receptionist_outreach
  Category: MARKETING
  Language: English (en)
  Body:     Hi {{1}}, are you still looking to fill the receptionist gap at {{2}}?
  Footer:   Reply STOP to opt out.
  Variables:
    {{1}} = contact name (or "there")
    {{2}} = company name

  Submit at: business.facebook.com → WhatsApp Manager → Message Templates → Create

WEBHOOK:
  Meta calls your webhook URL whenever a prospect replies.
  Set up at: developers.facebook.com → your app → WhatsApp → Configuration → Webhook
  The webhook calls handle_reply() which fires the pitch (Message 2) automatically.

Notes:
  - Only sends to Indian mobile numbers (starts 6–9, 10 digits)
  - Phone numbers scraped from company websites — may be landlines; module skips those
  - Template approval takes 1–2 days
  - Free tier: 1,000 business-initiated conversations/month (~1,000 cold messages)
  - After prospect replies: 24h free-form window opens → pitch fires automatically
"""
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
TEMPLATE_NAME = os.getenv("WHATSAPP_TEMPLATE_NAME", "receptionist_outreach")

# Channel provider: "meta" (default, official Cloud API) or "openwa" (unofficial,
# open-wa/wa-automate via a personal number — no approval, free, ban risk).
# Read lazily so tests/env changes take effect without re-import.
def _provider() -> str:
    return os.getenv("WHATSAPP_PROVIDER", "meta").strip().lower()

_API_URL = "https://graph.facebook.com/v20.0/{phone_number_id}/messages"

# Indian mobile: +91 prefix optional, 10 digits starting 6–9
_INDIA_MOBILE_RE = re.compile(r'(?:\+91[\s\-]?)?([6-9]\d{9})')


def _extract_mobile(phone: str) -> str | None:
    """Return E.164 Indian mobile number or None."""
    if not phone:
        return None
    match = _INDIA_MOBILE_RE.search(re.sub(r'\s', '', phone))
    if not match:
        return None
    return f"91{match.group(1)}"   # E.164 without '+' for Meta API


def _build_template_payload(to: str, data: dict) -> dict:
    """Build Meta API template message payload — question only, 2 variables."""
    contact = data.get("poster_name") or "there"
    company = data.get("company_name", "your business")

    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": TEMPLATE_NAME,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": contact},
                        {"type": "text", "text": company},
                    ],
                }
            ],
        },
    }


def send(data: dict) -> bool:
    """
    Send touch 1: WhatsApp template (question only).
    Returns True on success. Silently skips if unconfigured or no mobile number found.
    """
    if _provider() == "openwa":
        from modules import openwa
        return openwa.send(data)

    if not PHONE_NUMBER_ID or not ACCESS_TOKEN:
        log.debug("WhatsApp not configured (WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN missing)")
        return False

    mobile = _extract_mobile(data.get("phone", ""))
    if not mobile:
        log.debug("No valid Indian mobile for %s — skipping WhatsApp", data.get("company_name"))
        return False

    payload = _build_template_payload(mobile, data)
    url = _API_URL.format(phone_number_id=PHONE_NUMBER_ID)

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            msg_id = resp.json().get("messages", [{}])[0].get("id", "")
            log.info("WhatsApp touch 1 sent to %s (msg_id: %s)", mobile, msg_id)
            return True
        else:
            log.error("WhatsApp API error %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        log.error("WhatsApp send failed to %s: %s", mobile, e)
        return False


def send_pitch(to_mobile: str, niche: str) -> bool:
    """
    Send touch 2: the pitch — fires after prospect replies to the question template.
    Only valid within the 24h free-form window opened by their reply.

    Called by handle_reply() when Meta webhook receives an inbound message.
    """
    niche_str = niche or "service"
    text = (
        f"I build voice agents for {niche_str} businesses — "
        f"they answer calls and handle bookings automatically. "
        f"Want me to send a 2-min clip?"
    )
    return send_freeform(to_mobile, text)


def send_freeform(to_mobile: str, text: str) -> bool:
    """
    Send a freeform text message — only valid within a 24h reply window.
    Used by send_pitch() and reply_handler for warm follow-ups.
    """
    if _provider() == "openwa":
        from modules import openwa
        return openwa.send_freeform(to_mobile, text)

    if not PHONE_NUMBER_ID or not ACCESS_TOKEN:
        return False

    mobile = _extract_mobile(to_mobile) or to_mobile.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "to": mobile,
        "type": "text",
        "text": {"body": text},
    }
    url = _API_URL.format(phone_number_id=PHONE_NUMBER_ID)

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        log.error("WhatsApp freeform failed to %s: %s", to_mobile, e)
        return False


def handle_reply(webhook_payload: dict) -> bool:
    """
    Process an inbound WhatsApp message from Meta's webhook.
    Does NOT send pitch immediately — stores reply timestamp so the scheduled
    job can send it with a natural delay (feels human, not bot).

    Webhook payload shape (Meta Cloud API):
      {
        "entry": [{
          "changes": [{
            "value": {
              "messages": [{"from": "919876543210", "text": {"body": "..."}}],
              "contacts": [{"profile": {"name": "..."}}]
            }
          }]
        }]
      }

    Wire this to a POST /webhook endpoint (e.g. FastAPI on Railway).
    """
    from datetime import datetime, timezone
    from modules import sheets_writer

    try:
        messages = (
            webhook_payload
            .get("entry", [{}])[0]
            .get("changes", [{}])[0]
            .get("value", {})
            .get("messages", [])
        )
        if not messages:
            return False

        msg = messages[0]
        from_number = msg.get("from", "")   # e.g. "919876543210"
        body = (msg.get("text") or {}).get("body", "").strip()

        if not from_number:
            return False

        leads = sheets_writer.get_all_leads()

        # Opt-out check
        if re.search(r'\bSTOP\b', body, re.IGNORECASE):
            log.info("WhatsApp opt-out from %s", from_number)
            for lead in leads:
                if _extract_mobile(lead.get("phone", "")) == from_number:
                    sheets_writer.update_field(lead["slug"], "opted_out", "yes")
            return True

        # Look up lead by phone number
        lead = next(
            (l for l in leads if _extract_mobile(l.get("phone", "")) == from_number),
            None,
        )

        if not lead:
            log.debug("WhatsApp reply from unknown number %s — ignoring", from_number)
            return False

        slug = lead.get("slug", "")
        stage = lead.get("whatsapp_stage", "")

        if stage == "question_sent":
            # Store reply timestamp — pitch will be sent by process_whatsapp_replies()
            # on the next scheduled run (natural delay, doesn't feel like a bot)
            now = datetime.now(timezone.utc).isoformat()
            sheets_writer.update_field(slug, "whatsapp_stage", "reply_received")
            sheets_writer.update_field(slug, "whatsapp_reply_at", now)
            log.info("WhatsApp reply stored for %s — pitch queued for next run", lead.get("company_name"))
            return True

        log.info("WhatsApp reply from %s — stage=%s, no auto-action", lead.get("company_name"), stage)
        return True

    except Exception as e:
        log.error("handle_reply failed: %s", e)
        return False


# Minimum hours to wait before sending pitch after they reply.
# Feels like Shaurya checked his phone a few hours later, not a bot.
_PITCH_DELAY_HOURS = 2
_PITCH_MAX_HOURS = 22   # WhatsApp 24h window — don't send if too late


def process_whatsapp_replies(max_per_run: int = 20):
    """
    Called by the daily scheduler (3pm IST run).
    Finds leads who replied to the question template, waits at least
    _PITCH_DELAY_HOURS, then sends the pitch (touch 2).
    """
    from datetime import datetime, timedelta, timezone
    from modules import sheets_writer

    leads = sheets_writer.get_all_leads()
    sent = 0

    for lead in leads:
        if sent >= max_per_run:
            break

        if lead.get("whatsapp_stage") != "reply_received":
            continue
        if lead.get("opted_out", "no") == "yes":
            continue

        reply_at_str = lead.get("whatsapp_reply_at", "")
        if not reply_at_str:
            continue

        try:
            reply_at = datetime.fromisoformat(reply_at_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        now = datetime.now(timezone.utc)
        hours_since = (now - reply_at).total_seconds() / 3600

        # Too soon (feels like bot) or too late (24h window closed)
        if hours_since < _PITCH_DELAY_HOURS or hours_since > _PITCH_MAX_HOURS:
            continue

        phone = lead.get("phone", "")
        mobile = _extract_mobile(phone)
        if not mobile:
            continue

        niche = lead.get("niche", "service")
        ok = send_pitch(mobile, niche)
        if ok:
            sheets_writer.update_field(lead["slug"], "whatsapp_stage", "pitch_sent")
            log.info(
                "WhatsApp pitch sent to %s (%.1fh after reply)",
                lead.get("company_name"), hours_since,
            )
            sent += 1
        else:
            log.error("WhatsApp pitch failed for %s", lead.get("company_name"))

    if sent:
        log.info("process_whatsapp_replies: sent %d pitches", sent)
