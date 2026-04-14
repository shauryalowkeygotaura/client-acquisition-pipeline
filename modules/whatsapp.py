"""
modules/whatsapp.py — WhatsApp outreach via Meta Cloud API (free tier)

Setup (one-time):
  1. business.facebook.com → Create a Business → Add a WhatsApp account
  2. developers.facebook.com → Create App → Add WhatsApp product
  3. WhatsApp → Getting Started → note your Phone Number ID + temporary token
  4. Generate a permanent token:
       System Users → Create system user → Generate token → check whatsapp_business_messaging
  5. Add your real number under WhatsApp → Phone Numbers → Add phone number
  6. Submit cold outreach template (see TEMPLATE section below)

Required Doppler secrets:
  WHATSAPP_PHONE_NUMBER_ID   — from Meta dashboard (numeric ID, not the phone number)
  WHATSAPP_ACCESS_TOKEN      — permanent system user token
  WHATSAPP_TEMPLATE_NAME     — name of your approved template (default: receptionist_outreach)

TEMPLATE to submit for approval in Meta Business Manager:
  Name:     receptionist_outreach
  Category: MARKETING
  Language: English (en)
  Body:     Hi {{1}}, are you still looking to fill the receptionist gap at {{2}}?

            I build voice agents for {{3}} businesses — they answer calls and handle
            bookings automatically. Want me to send a 2-min clip?
  Footer:   Reply STOP to opt out.
  Variables:
    {{1}} = contact name (or "there")
    {{2}} = company name
    {{3}} = industry/niche

  Submit at: business.facebook.com → WhatsApp Manager → Message Templates → Create

Notes:
  - Only sends to Indian mobile numbers (starts 6–9, 10 digits)
  - Phone numbers scraped from company websites — may be landlines; module skips those
  - Template approval takes 1–2 days
  - Free tier: 1,000 business-initiated conversations/month (~1,000 cold messages)
  - After prospect replies: 24h free-form window for follow-ups
"""
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
TEMPLATE_NAME = os.getenv("WHATSAPP_TEMPLATE_NAME", "receptionist_outreach")

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
    """Build Meta API template message payload."""
    contact = data.get("poster_name") or "there"
    company = data.get("company_name", "your business")
    niche = data.get("niche") or data.get("industry") or "service"

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
                        {"type": "text", "text": niche},
                    ],
                }
            ],
        },
    }


def send(data: dict) -> bool:
    """
    Send a WhatsApp template message via Meta Cloud API.
    Returns True on success. Silently skips if unconfigured or no mobile number found.
    """
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
            log.info("WhatsApp sent to %s (msg_id: %s)", mobile, msg_id)
            return True
        else:
            log.error("WhatsApp API error %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        log.error("WhatsApp send failed to %s: %s", mobile, e)
        return False


def send_freeform(to_mobile: str, text: str) -> bool:
    """
    Send a freeform text message — only valid within a 24h reply window.
    Used by reply_handler for warm follow-ups after a prospect has messaged back.
    """
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
