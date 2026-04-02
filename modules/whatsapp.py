"""
modules/whatsapp.py — WhatsApp outreach via Twilio

Setup (free sandbox for testing):
  1. Sign up at twilio.com — no credit card needed for sandbox
  2. In Twilio Console → Messaging → Try it out → Send a WhatsApp message
  3. Your sandbox number: whatsapp:+14155238886
  4. To activate sandbox: recipient texts "join <your-sandbox-word>" to +1 (415) 523-8886
  5. For production: get a Twilio number with WhatsApp Business API enabled

Required .env vars:
  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_AUTH_TOKEN=your_auth_token
  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   (sandbox) or whatsapp:+your_twilio_number

Notes:
  - Only sends to Indian mobile numbers (starts with 6–9, 10 digits)
  - Phone numbers are scraped from company websites — may be landlines
  - Whatsapp is most effective for high-score leads where you have a mobile number
  - Message length limit: 1600 chars (we stay well under)
"""
import logging
import os
import re

log = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Indian mobile number pattern: +91 followed by 10 digits starting with 6–9
# Also matches 10-digit local format starting with 6–9
_INDIA_MOBILE_RE = re.compile(r'(?:\+91[\s\-]?)?([6-9]\d{9})')


def _extract_mobile(phone: str) -> str | None:
    """Extract a clean Indian mobile number from a scraped phone string. Returns E.164 or None."""
    if not phone:
        return None
    match = _INDIA_MOBILE_RE.search(re.sub(r'\s', '', phone))
    if not match:
        return None
    digits = match.group(1)
    return f"+91{digits}"


def _build_message(data: dict) -> str:
    """
    Build a short WhatsApp message from the lead's linkedin_msg field.
    WhatsApp is more casual than email — use the linkedin_msg copy (already conversational).
    Append the booking link at the end.
    """
    from modules.reply_handler import BOOKING_LINK

    base = data.get("linkedin_msg", "")
    if not base:
        company = data.get("company_name", "your business")
        niche = data.get("niche", "")
        base = (
            f"Hi — saw you're hiring a receptionist at {company}. "
            f"I build AI voice agents for {niche or 'service'} businesses that handle calls "
            f"during the hiring gap. Worth a quick look?"
        )

    # Keep it short for WhatsApp — truncate if needed
    if len(base) > 400:
        base = base[:397] + "..."

    return f"{base}\n\n— Shaurya\n{BOOKING_LINK}"


def send(data: dict) -> bool:
    """
    Send a WhatsApp message to the lead's phone number.
    Returns True if sent successfully.
    Silently skips if no mobile number, Twilio not configured, or number isn't mobile.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        log.debug("Twilio not configured — skipping WhatsApp send")
        return False

    phone_raw = data.get("phone", "")
    mobile = _extract_mobile(phone_raw)
    if not mobile:
        log.debug("No valid Indian mobile number for %s — skipping WhatsApp", data.get("company_name"))
        return False

    try:
        from twilio.rest import Client  # lazy import — twilio is optional dep
    except ImportError:
        log.warning("twilio package not installed. Run: pip install twilio")
        return False

    message_body = _build_message(data)
    to_whatsapp = f"whatsapp:{mobile}"

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            body=message_body,
            from_=TWILIO_WHATSAPP_FROM,
            to=to_whatsapp,
        )
        log.info("WhatsApp sent to %s (SID: %s)", mobile, msg.sid)
        return True
    except Exception as e:
        log.error("WhatsApp send failed to %s: %s", mobile, e)
        return False
